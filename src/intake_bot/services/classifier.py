import asyncio
import inspect
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import yaml
from intake_bot.models.classifier import (
    ClassificationResponse,
    FollowUpQuestion,
)
from intake_bot.services.reference_data import ReferenceDataLoader
from intake_bot.utils.ev import require_ev
from intake_bot.utils.globals import DATA_DIR, DEBUG
from loguru import logger
from openai import AsyncOpenAI
from rapidfuzz import fuzz, process, utils


class Classifier:
    """Async classification service for legal problem intake.

    Integrates multiple classifier providers and aggregates their results
    using weighted voting.
    """

    @staticmethod
    def _load_prompts() -> Dict[str, str]:
        prompts_file = Path(DATA_DIR) / "classifier_prompts.yml"
        with open(prompts_file) as f:
            prompts_data: Dict[str, str] = yaml.safe_load(f)
        return prompts_data

    @staticmethod
    def _load_taxonomy() -> Dict[str, str]:
        return ReferenceDataLoader().legal_problem_codes

    def __init__(self):
        # Load data from files
        self.prompts = Classifier._load_prompts()
        self.taxonomy = Classifier._load_taxonomy()

        # Follow-up threshold: ask follow-up questions if confidence is below this
        self.follow_up_threshold = 0.70

        # Default weights
        self.model_weights = {
            # "gpt-4o-mini": 0.85,
            "gpt-4.1-mini": 0.87,
            "gemini-2.5-flash-lite": 0.9,
            "gpt-5-nano": 0.9,
            "keyword": 0.5,
        }

        # Default enabled classifiers
        self.enabled_models = [
            # "gpt-4o-mini",
            "gpt-4.1-mini",
            "gemini-2.5-flash-lite",
            "gpt-5-nano",
            "keyword",
        ]

        self.providers = self._init_providers()

    def load_prompt(self, taxonomy: List[str]) -> str:
        """Load and render a prompt template for a provider.

        Args:
          taxonomy: List of normalized labels (legal problem categories).

        Returns:
          The `final_prompt` with the taxonomy injected.
        """
        prompt_template = self.prompts.get("default", "")
        # Build taxonomy string from the normalized labels
        taxonomy_str = "\n".join(taxonomy)
        if not taxonomy_str:
            taxonomy_str = "No specific legal taxonomy categories were provided or loaded."
        final_prompt = prompt_template.replace("{{taxonomy}}", taxonomy_str)
        return final_prompt

    def _init_providers(
        self, enabled_providers_override: Optional[List[str]] = None
    ) -> List["Classifier.Provider"]:
        """Instantiate and filter providers based on config or override.

        Args:
          enabled_providers_override: Restrict enabled providers to these instance names.

        Returns:
          List of enabled provider instances.
        """
        all_providers = []

        # Try to initialize OpenAI providers
        try:
            all_providers.append(self.OpenAIProvider(model_name="gpt-4o-mini"))
        except ValueError as e:
            logger.warning(f"""Could not initialize gpt-4o-mini provider: {e}""")

        try:
            all_providers.append(self.OpenAIProvider(model_name="gpt-4.1-mini"))
        except ValueError as e:
            logger.warning(f"""Could not initialize gpt-4.1-mini provider: {e}""")

        try:
            all_providers.append(self.OpenAIProvider(model_name="gpt-5-nano"))
        except ValueError as e:
            logger.warning(f"""Could not initialize gpt-5-nano provider: {e}""")

        # Try to initialize Gemini provider
        try:
            all_providers.append(self.GeminiProvider(model_name="gemini-2.5-flash-lite"))
        except ValueError as e:
            logger.warning(f"""Could not initialize gemini-2.5-flash-lite provider: {e}""")

        # Always add keyword provider
        try:
            all_providers.append(self.KeywordProvider())
        except Exception as e:
            logger.warning(f"""Could not initialize keyword provider: {e}""")

        # Filter by enabled providers
        enabled = enabled_providers_override or self.enabled_models
        filtered_providers = [p for p in all_providers if p.model_name in enabled]

        return filtered_providers

    async def _semantically_merge_questions(
        self, questions: List[FollowUpQuestion]
    ) -> List[FollowUpQuestion]:
        """Merge similar follow-up questions using an LLM call.

        Args:
          questions: A list of candidate follow-up questions.

        Returns:
          A deduplicated list of merged questions.
        """
        if not questions:
            return []

        questions_data = [q.model_dump() for q in questions]
        questions_json = json.dumps(questions_data, indent=2)

        provider = self.GeminiProvider(model_name="gemini-2.5-flash-lite")

        try:

            async def _make_request():
                if DEBUG:
                    start_time = time.time()
                    logger.debug(
                        f"""[{provider.model_name}] Starting API call for question deduplication"""
                    )

                response = await provider.client.chat.completions.create(
                    model=provider.model_name,
                    messages=[
                        {"role": "system", "content": self.prompts.get("semantic_merge", "")},
                        {
                            "role": "user",
                            "content": questions_json,
                        },
                    ],
                    response_format={"type": "json_object"},
                )

                if DEBUG:
                    elapsed = time.time() - start_time
                    logger.debug(
                        f"""[{provider.model_name}] API call completed in {elapsed:.2f}s"""
                    )

                return response

            response = await provider._call_with_retry_async(_make_request)
            content = response.choices[0].message.content
            parsed_response = json.loads(content)

            merged_questions_data = parsed_response.get("merged_questions", [])

            merged_questions = []
            for q_data in merged_questions_data:
                if "question" in q_data:
                    merged_questions.append(
                        FollowUpQuestion(
                            question=q_data.get("question"),
                            format=q_data.get("format"),
                            options=q_data.get("options"),
                        )
                    )
                else:
                    logger.warning(f"""Merged question data missing 'question' key: {q_data}""")

            return merged_questions

        except json.JSONDecodeError as e:
            error_message = f"""JSON decoding error during semantic merging: {e}"""
            logger.error(error_message)
            return questions
        except Exception as e:
            error_message = f"""An unexpected error occurred during semantic merging: {e}"""
            logger.error(error_message)
            return questions

    async def _get_voted_results(
        self,
        results_with_providers: List[Tuple[str, Union[Dict[str, Any], Exception]]],
        taxonomy_dict: Optional[Dict[str, str]] = None,
    ) -> ClassificationResponse:
        """Combine multiple provider results by weighted voting.

        Args:
          results_with_providers: A list of (provider_model_name, result_or_exception) tuples.
          taxonomy_dict: Dict mapping normalized labels to original full entries.

        Returns:
          A ClassificationResponse with aggregated labels and questions.
        """
        label_scores = defaultdict(float)
        raw_provider_results = {}
        all_questions_by_provider = {}

        for provider_model_name, result in results_with_providers:
            base_weight = self.model_weights.get(provider_model_name, 1.0)
            if isinstance(result, Exception):
                logger.error(f"""Classifier provider '{provider_model_name}' failed: {result}""")
                raw_provider_results[provider_model_name] = {"error": str(result)}
            else:
                # Serialize result for debug output (convert FollowUpQuestion objects to dicts if needed)
                serialized_result = result.copy()
                if "questions" in serialized_result and isinstance(
                    serialized_result["questions"], list
                ):
                    serialized_result["questions"] = [
                        q.model_dump() if isinstance(q, FollowUpQuestion) else q
                        for q in serialized_result["questions"]
                    ]
                raw_provider_results[provider_model_name] = serialized_result

                # Aggregate label scores
                for label_entry in result.get("labels", []):
                    label_str = label_entry.get("legal_problem_code")
                    confidence = label_entry.get("confidence", 1.0)

                    if label_str and label_str in taxonomy_dict:
                        weighted_score = base_weight * confidence
                        label_scores[label_str] += weighted_score

                # Collect questions from this provider
                all_questions_by_provider[provider_model_name] = result.get("questions", [])

        # Normalize label scores to 0-1 range based on max possible score
        max_possible_score = sum(self.model_weights.get(p[0], 1.0) for p in results_with_providers)
        if max_possible_score > 0:
            normalized_label_scores = {
                label: score / max_possible_score for label, score in label_scores.items()
            }
        else:
            normalized_label_scores = label_scores

        # Sort labels by normalized scores
        sorted_labels = sorted(
            normalized_label_scores.items(), key=lambda item: item[1], reverse=True
        )

        # Collect questions, prioritizing by provider weight
        all_question_objs = []
        seen_questions = set()
        for provider_model_name in sorted(
            all_questions_by_provider.keys(),
            key=lambda name: self.model_weights.get(name, 1.0),
            reverse=True,
        ):
            for question_entry in all_questions_by_provider[provider_model_name]:
                if isinstance(question_entry, dict):
                    question_text = question_entry.get("question")
                    format_type = question_entry.get("format") or question_entry.get("type")
                    options = question_entry.get("options")
                elif isinstance(question_entry, str):
                    question_text = question_entry
                    format_type = None
                    options = None
                else:
                    continue

                if question_text and question_text not in seen_questions:
                    seen_questions.add(question_text)
                    all_question_objs.append(
                        FollowUpQuestion(
                            question=question_text, format=format_type, options=options
                        )
                    )

        # Merge questions semantically
        merged_questions = await self._semantically_merge_questions(all_question_objs)
        final_top_questions = merged_questions[:3] if merged_questions else []

        # Debug log all labels that survived voting
        if DEBUG and sorted_labels:
            logger.debug(f"""All voted labels (top {len(sorted_labels)}):""")
            for label, score in sorted_labels:
                original_label = taxonomy_dict.get(label, label) if taxonomy_dict else label
                logger.debug(f"""  - {original_label} (score: {score:.3f})""")

        # Take top 1 label only if it exists in taxonomy
        top_legal_problem_code = None
        top_confidence = None
        if sorted_labels:
            label, score = sorted_labels[0]
            if taxonomy_dict and label in taxonomy_dict:
                top_legal_problem_code = taxonomy_dict[label]
                top_confidence = score

        # Determine is_eligible: False if code starts with "00", True otherwise
        is_eligible = True
        if top_legal_problem_code and top_legal_problem_code.startswith("00"):
            is_eligible = False

        # Determine if we should include follow-up questions based on confidence
        questions_to_include = None
        if top_confidence is not None and top_confidence < self.follow_up_threshold:
            questions_to_include = final_top_questions

        if not top_legal_problem_code and not final_top_questions:
            response_data = {
                "follow_up_questions": [
                    FollowUpQuestion(
                        question="All classifier providers failed to return a response or no labels/questions were found."
                    )
                ],
            }
        else:
            response_data = {
                "legal_problem_code": top_legal_problem_code,
                "confidence": top_confidence,
                "is_eligible": is_eligible,
                "follow_up_questions": questions_to_include,
            }

        if DEBUG:
            response_data["raw_provider_results"] = raw_provider_results
            response_data["weighted_label_scores"] = dict(normalized_label_scores)

        logger.info(f"""_get_voted_results response_data: {response_data}""")
        return ClassificationResponse(**response_data)

    async def classify(
        self,
        problem_description: str,
    ) -> ClassificationResponse:
        """Classify a legal problem using enabled providers.

        Args:
          problem_description: Natural language description of the problem.
          enabled_models: Override which models to use.

        Returns:
          A ClassificationResponse with labels and follow-up questions.
        """
        if DEBUG:
            overall_start = time.time()
            logger.debug("=" * 60)
            logger.debug("Starting classification request")
            logger.debug(f"""Problem: {problem_description[:100]}...""")
            logger.debug("=" * 60)

        taxonomy = self.taxonomy
        if not taxonomy:
            return ClassificationResponse(
                follow_up_questions=[FollowUpQuestion(question="Taxonomy could not be loaded.")],
            )

        if not self.providers:
            return ClassificationResponse(
                follow_up_questions=[
                    FollowUpQuestion(question="No providers available for classification.")
                ],
            )

        # Run providers in parallel
        tasks = []
        taxonomy_keys = list(taxonomy.keys())
        final_prompt = self.load_prompt(taxonomy_keys)
        for provider in self.providers:
            provider_kwargs = {
                "problem_description": problem_description,
                "prompt": final_prompt,
                "taxonomy": taxonomy_keys,
            }
            # Add reasoning_effort if provider has it set
            if provider.reasoning_effort:
                provider_kwargs["reasoning_effort"] = provider.reasoning_effort
            tasks.append(
                (provider.model_name, asyncio.create_task(provider.classify(**provider_kwargs)))
            )

        # Gather results with timeout per provider
        results_with_providers = []
        for provider_name, task in tasks:
            try:
                result = await asyncio.wait_for(task, timeout=Classifier.Provider.PROVIDER_TIMEOUT)
                results_with_providers.append((provider_name, result))
            except asyncio.TimeoutError:
                error_msg = f"""Provider timeout after {Classifier.Provider.PROVIDER_TIMEOUT}s"""
                logger.error(f"""Provider {provider_name} failed: {error_msg}""")
                results_with_providers.append((provider_name, TimeoutError(error_msg)))
            except Exception as e:
                logger.error(f"""Provider {provider_name} failed: {e}""")
                results_with_providers.append((provider_name, e))

        # Always use weighted voting for aggregation
        response = await self._get_voted_results(results_with_providers, taxonomy)

        if DEBUG:
            overall_elapsed = time.time() - overall_start
            logger.debug("=" * 60)
            logger.debug(f"""Classification completed in {overall_elapsed:.2f}s""")
            logger.debug(f"""Top label: {response.legal_problem_code or "None"}""")
            logger.debug("=" * 60)

        return response

    def __repr__(self):
        return f"""Fetch(classifiers={self.enabled_models})"""

    # ----------------------------------------------------------------
    # Provider classes
    # ----------------------------------------------------------------

    class Provider:
        """Base class for LLM providers with built-in retry and rate-limit handling."""

        # Retry configuration - optimized for real-time voice bot calls
        # Callers won't wait more than ~60 seconds, so we need quick feedback
        MAX_RETRIES = 5
        BASE_WAIT_TIME = 0.5  # seconds
        MAX_WAIT_TIME = 15.0  # 15 seconds max per retry
        PROVIDER_TIMEOUT = 45.0  # 45 seconds max for any single provider call

        client: Optional[AsyncOpenAI] = None
        reasoning_effort: Optional[Literal["minimal", "low", "medium", "high"]] = None

        def __init__(self, model_name: str):
            """Initialize provider.

            Args:
              model_name: The model name to use for classification.
            """
            self.model_name = model_name

        @staticmethod
        def _is_rate_limit_error(exc: BaseException) -> bool:
            """Check if exception is a rate limit error.

            Args:
              exc: The exception to check.

            Returns:
              True if the exception represents a rate limit error.
            """
            msg = str(exc).lower()
            if "rate limit" in msg or "429" in msg or "too many requests" in msg:
                return True

            # Check for HTTP 429 status code
            resp = getattr(exc, "response", None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
                if status == 429:
                    return True

            return False

        @staticmethod
        def _parse_retry_after(exc: BaseException) -> Optional[float]:
            """Parse retry delay from exception.

            Attempts to extract delay from:
            1. Retry-After header
            2. Error message text

            Args:
              exc: The exception to parse.

            Returns:
              Delay in seconds, or None if not found.
            """
            # Try to parse from Retry-After header
            resp = getattr(exc, "response", None)
            if resp is not None and hasattr(resp, "headers"):
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    try:
                        return max(1.0, min(float(retry_after), Classifier.Provider.MAX_WAIT_TIME))
                    except (ValueError, TypeError):
                        pass

            # Try to parse from error message
            msg = str(exc).lower()
            match = re.search(r"try again in (\d+\.?\d*)s", msg)
            if match:
                try:
                    delay = float(match.group(1))
                    return max(1.0, min(delay, Classifier.Provider.MAX_WAIT_TIME))
                except (ValueError, TypeError):
                    pass

            return None

        @staticmethod
        def _calculate_backoff_delay(attempt: int) -> float:
            """Calculate exponential backoff delay with jitter.

            Args:
              attempt: The attempt number (0-indexed).

            Returns:
              Delay in seconds.
            """
            import random

            # Exponential backoff: 2^attempt, but capped
            delay = min(
                Classifier.Provider.BASE_WAIT_TIME * (2**attempt), Classifier.Provider.MAX_WAIT_TIME
            )
            # Add jitter: ±25%
            jitter = delay * 0.25 * (2 * random.random() - 1)
            return delay + jitter

        async def _call_with_retry_async(self, async_func, *args, **kwargs) -> Any:
            """Execute async function with retry and rate-limit handling.

            Args:
              async_func: Async function to call.
              *args: Positional arguments for the function.
              **kwargs: Keyword arguments for the function.

            Returns:
              The result of the function call.

            Raises:
              The last exception if all retries are exhausted.
            """
            last_exception = None

            for attempt in range(self.MAX_RETRIES):
                try:
                    if DEBUG and attempt > 0:
                        logger.debug(
                            f"""[{self.model_name}] Attempt {attempt + 1}/{self.MAX_RETRIES}"""
                        )
                    return await async_func(*args, **kwargs)

                except Exception as e:
                    last_exception = e

                    if not self._is_rate_limit_error(e):
                        # Not a rate limit error, re-raise immediately
                        raise

                    if attempt == self.MAX_RETRIES - 1:
                        # Last attempt, will re-raise after this
                        logger.error(
                            f"""[{self.model_name}] Max retries ({self.MAX_RETRIES}) exhausted"""
                        )
                        break

                    # Calculate wait time
                    wait_time = self._parse_retry_after(e)
                    if wait_time is None:
                        wait_time = self._calculate_backoff_delay(attempt)

                    if DEBUG:
                        logger.debug(
                            f"""[{self.model_name}] Rate limited. Waiting {wait_time:.1f}s before retry"""
                        )

                    await asyncio.sleep(wait_time)

            if last_exception:
                raise last_exception
            raise RuntimeError(f"""Failed after {self.MAX_RETRIES} attempts""")

        async def classify(
            self,
            problem_description: str,
            prompt: str,
            reasoning_effort: Optional[Literal["minimal", "low", "medium", "high"]] = None,
            **kwargs,
        ) -> Dict[str, Any]:
            """Common classification logic for OpenAI-compatible clients.

            Uses self.client which should be an AsyncOpenAI instance.

            Args:
              problem_description: The problem description to classify.
              prompt: The prompt template to use.
              reasoning_effort: Reasoning effort level for gpt-5 models. Can be "minimal", "low", "medium", or "high".
              **kwargs: Additional arguments (ignored).

            Returns:
              Dict with 'labels' and 'questions' keys.
            """
            try:

                async def _make_request():
                    if DEBUG:
                        start_time = time.time()
                        logger.debug(f"""[{self.model_name}] Starting API call""")

                    # Build request parameters
                    request_params = {
                        "model": self.model_name,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": problem_description},
                        ],
                        "response_format": {"type": "json_object"},
                    }

                    # Add reasoning_effort if provided and client supports it
                    if reasoning_effort:
                        try:
                            sig = inspect.signature(self.client.chat.completions.create)
                            if "reasoning_effort" in sig.parameters:
                                request_params["reasoning_effort"] = reasoning_effort
                                if DEBUG:
                                    logger.debug(
                                        f"""[{self.model_name}] Using reasoning_effort={reasoning_effort}"""
                                    )
                        except Exception:
                            pass

                    response = await self.client.chat.completions.create(**request_params)

                    if DEBUG:
                        elapsed = time.time() - start_time
                        logger.debug(
                            f"""[{self.model_name}] API call completed in {elapsed:.2f}s"""
                        )

                    return response

                response = await self._call_with_retry_async(_make_request)
                content = response.choices[0].message.content
                parsed_response = json.loads(content)
                logger.debug(f"""[{self.model_name}] {parsed_response}""")

                labels = []
                questions = []

                if not parsed_response.get("likely_no_legal_problem"):
                    # Handle categories/labels - convert to dicts with legal_problem_code field
                    for cat in parsed_response.get("categories", []):
                        labels.append({"legal_problem_code": cat, "confidence": 1.0})

                    for item in parsed_response.get("labels", []):
                        if isinstance(item, str):
                            labels.append({"legal_problem_code": item, "confidence": 1.0})
                        elif isinstance(item, dict) and item.get("label"):
                            labels.append(
                                {
                                    "legal_problem_code": item.get("label"),
                                    "confidence": item.get("confidence", 1.0),
                                }
                            )

                    # Handle questions
                    for q in parsed_response.get("followup_questions", []):
                        if isinstance(q, dict) and q.get("question"):
                            questions.append(q)

                    for q in parsed_response.get("questions", []):
                        if isinstance(q, dict) and q.get("question"):
                            questions.append(q)
                        elif isinstance(q, str):
                            questions.append({"question": q})

                return {"labels": labels, "questions": questions}

            except json.JSONDecodeError as e:
                error_msg = f"""JSON decoding error from {self.model_name}: {e}"""
                logger.error(error_msg)
                return {"labels": [], "questions": [], "error": error_msg}
            except Exception as e:
                error_msg = f"""Error from {self.model_name}: {e}"""
                logger.error(error_msg)
                return {"labels": [], "questions": [], "error": error_msg}

    class OpenAIProvider(Provider):
        def __init__(self, model_name: str = "gpt-4o"):
            """Initialize OpenAI provider.

            Args:
              model_name: The OpenAI model to use.
            """
            super().__init__(model_name)
            api_key = require_ev("OPENAI_API_KEY")
            self.client = AsyncOpenAI(api_key=api_key)
            if model_name.startswith("gpt-5"):
                self.reasoning_effort = "minimal"

    class GeminiProvider(Provider):
        """
        Uses the OpenAI-compatible API endpoint for Gemini as documented at:
        https://ai.google.dev/gemini-api/docs/openai
        """

        def __init__(self, model_name: str = "gemini-2.5-flash-lite"):
            """Initialize Gemini provider.

            Args:
              model_name: The Gemini model to use.
            """
            super().__init__(model_name)
            api_key = require_ev("GOOGLE_API_KEY")
            # Use OpenAI-compatible API for Gemini
            self.client = AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=api_key,
            )

    class KeywordProvider(Provider):
        """Simple keyword-based provider."""

        def __init__(self):
            """Initialize keyword provider."""
            super().__init__("keyword")

        async def classify(
            self, problem_description: str, taxonomy: List[str], **kwargs
        ) -> Dict[str, Any]:
            """Classify using fuzzy keyword matching with rapidfuzz.

            Combines fuzzy string matching with direct word matching to find
            relevant legal categories from the problem description.
            """
            if taxonomy is None:
                return {"labels": [], "questions": []}
            words_to_ignore = {
                "the",
                "a",
                "an",
                "i",
                "my",
                "me",
                "is",
                "am",
                "are",
                "been",
                "have",
                "has",
                "do",
                "does",
                "did",
                "to",
                "from",
                "for",
                "of",
                "and",
                "or",
                "but",
                "in",
                "on",
                "at",
                "by",
                "with",
                "about",
                "not",
                "what",
                "no",
                "know",
                "dont",
                "don't",
                "can",
                "would",
                "could",
                "should",
                "may",
                "will",
                "just",
                "been",
                "being",
                "been",
                "be",
                "he",
                "she",
                "we",
                "they",
            }

            words = problem_description.lower().split()
            key_terms = set(
                w.rstrip(".,!?;:()[]{}").lower()
                for w in words
                if len(w) > 3 and w.rstrip(".,!?;:()[]{}").lower() not in words_to_ignore
            )
            key_phrase = " ".join(sorted(key_terms))  # All key terms for stronger signal

            if not key_phrase.strip():
                key_phrase = problem_description  # Fall back to full description if no key terms

            # Use rapidfuzz to find matching categories with fuzzy matching
            matches = process.extract(
                key_phrase,
                taxonomy,
                scorer=fuzz.WRatio,
                score_cutoff=48,  # Slightly lower threshold to catch relevant matches
                limit=None,
                processor=utils.default_process,
            )

            # Post-process matches to boost scores when key terms directly appear in category
            # Only keep categories where we find direct word matches OR the score is high enough
            filtered_labels = {}
            for category, score, index in matches:
                # Bonus if any key terms appear as whole words in the category
                category_lower = category.lower()
                direct_match_bonus = 0
                for term in key_terms:
                    # Check for whole word matches (not substring)
                    if (
                        f""" {term} """ in f""" {category_lower} """
                        or category_lower.startswith(term + " ")
                        or category_lower.endswith(f""" {term}""")
                    ):
                        direct_match_bonus += 5  # +5% for each direct word match

                # Only accept matches that either:
                # 1. Have direct word matches (direct_match_bonus > 0)
                # 2. Have high fuzzy match score (> 65%)
                if direct_match_bonus > 0 or score > 65:
                    final_score = min(100, score + direct_match_bonus)
                    filtered_labels[category] = final_score / 100.0

            # Sort by score and return top matches as dicts
            sorted_matches = sorted(filtered_labels.items(), key=lambda x: x[1], reverse=True)
            labels = [
                {"legal_problem_code": label, "confidence": conf} for label, conf in sorted_matches
            ]
            logger.debug(f"""[KeywordProvider] {labels}""")
            return {"labels": labels, "questions": []}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python classifier.py '<legal problem description>'")
        print(
            "Example: python classifier.py 'I am going through a divorce and need help with custody'"
        )
        sys.exit(1)

    problem_description = sys.argv[1]

    print(f"""Classifying: {problem_description}\n""")

    classifier = Classifier()
    result = asyncio.run(classifier.classify(problem_description))

    print("Classification Results:")
    print(json.dumps(result.model_dump(), indent=2))
