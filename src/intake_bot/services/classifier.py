import asyncio
import inspect
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from openai import AsyncAzureOpenAI
from rapidfuzz import fuzz, process, utils

from intake_bot.models.classifier import (
    ClassificationResponse,
    FollowUpQuestion,
    ProviderLabel,
    ProviderQuestion,
    ProviderResult,
    ProviderStatus,
)
from intake_bot.services.reference_data import ReferenceDataLoader
from intake_bot.utils.ev import require_ev
from intake_bot.utils.globals import DATA_DIR, DEBUG


class Classifier:
    """Async classification service for legal problem intake.

    Integrates multiple classifier providers and aggregates their results
    using weighted voting.
    """

    @staticmethod
    def _load_prompts() -> dict[str, str]:
        prompts_file = Path(DATA_DIR) / "classifier_prompts.yml"
        with open(prompts_file) as f:
            prompts_data: dict[str, str] = yaml.safe_load(f)
        return prompts_data

    @staticmethod
    def _load_taxonomy() -> dict[str, str]:
        return ReferenceDataLoader().legal_problem_codes

    def __init__(self):
        # Load data from files
        self.prompts = Classifier._load_prompts()
        self.taxonomy = Classifier._load_taxonomy()

        # Follow-up threshold: ask follow-up questions if confidence is below this
        self.follow_up_threshold = 0.70

        # Default weights
        self.model_weights = {
            "gpt-4.1-mini": 0.87,
            "gpt-5-nano": 0.9,
            "keyword": 0.5,
        }

        # Default enabled classifiers
        self.enabled_models = [
            "gpt-4.1-mini",
            "gpt-5-nano",
            "keyword",
        ]

        self.providers = self._init_providers()

    def load_prompt(self, taxonomy: list[str]) -> str:
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
            taxonomy_str = (
                "No specific legal taxonomy categories were provided or loaded."
            )
        final_prompt = prompt_template.replace("{{taxonomy}}", taxonomy_str)
        return final_prompt

    def _init_providers(
        self, enabled_providers_override: list[str] | None = None
    ) -> list["Classifier.Provider"]:
        """Instantiate and filter providers based on config or override.

        Args:
          enabled_providers_override: Restrict enabled providers to these instance names.

        Returns:
          List of enabled provider instances.
        """
        all_providers = []

        # Try to initialize Azure OpenAI providers
        try:
            all_providers.append(self.AzureOpenAIProvider(model_name="gpt-4.1-mini"))
        except ValueError as e:
            logger.warning(f"""Could not initialize gpt-4.1-mini provider: {e}""")

        try:
            all_providers.append(self.AzureOpenAIProvider(model_name="gpt-5-nano"))
        except ValueError as e:
            logger.warning(f"""Could not initialize gpt-5-nano provider: {e}""")

        # Always add keyword provider
        try:
            all_providers.append(self.KeywordProvider())
        except Exception as e:  # noqa: BLE001 - an optional provider must not block startup
            logger.warning(f"""Could not initialize keyword provider: {e}""")

        # Filter by enabled providers
        enabled = enabled_providers_override or self.enabled_models
        filtered_providers = [p for p in all_providers if p.model_name in enabled]

        return filtered_providers

    @staticmethod
    def _compatible_questions(a: FollowUpQuestion, b: FollowUpQuestion) -> bool:
        """True if two questions are compatible for dedup (similar text, same
        normalized format, same normalized option set)."""
        _SIMILARITY_THRESHOLD = 94
        a_norm = a.question.strip().lower()
        b_norm = b.question.strip().lower()
        similarity = fuzz.token_set_ratio(a_norm, b_norm)
        if similarity < _SIMILARITY_THRESHOLD:
            return False
        fmt_a = Classifier.Provider._normalize_format(a.format)
        fmt_b = Classifier.Provider._normalize_format(b.format)
        if fmt_a != fmt_b:
            return False
        opts_a = Classifier.Provider._normalized_options_key(a.options, fmt_a)
        opts_b = Classifier.Provider._normalized_options_key(b.options, fmt_b)
        return opts_a == opts_b

    @staticmethod
    def _deterministic_deduplicate(
        questions: list[FollowUpQuestion],
    ) -> list[FollowUpQuestion]:
        """Deduplicate questions deterministically using RapidFuzz token_set_ratio.

        Merges similar questions at a high threshold (94) only when answer format
        and normalized option sets are compatible. Questions with the same/similar
        text but different formats or incompatible options remain distinct.
        Keeps the version from the highest-weight provider (questions should be
        ordered by provider weight descending), and returns at most 3 unique questions.
        """
        if not questions:
            return []

        merged: list[FollowUpQuestion] = []

        for q in questions:
            is_dup = False
            for existing in merged:
                if Classifier._compatible_questions(existing, q):
                    is_dup = True
                    break
            if not is_dup:
                merged.append(q)

        return merged[:3]

    async def _get_voted_results(
        self,
        results: list[ProviderResult],
        taxonomy_dict: dict[str, str] | None = None,
        language: str = "English",
    ) -> ClassificationResponse:
        """Combine multiple provider results by weighted voting.

        Args:
          results: A list of ProviderResult envelopes.
          taxonomy_dict: Dict mapping normalized labels to original full entries.

        Returns:
          A ClassificationResponse with aggregated labels and questions.
        """
        label_scores = defaultdict(float)
        raw_provider_results = {}
        all_questions_by_provider = {}

        for provider_result in results:
            model_name = provider_result.model_name
            base_weight = self.model_weights.get(model_name, 1.0)
            if provider_result.status in (
                ProviderStatus.FAILURE,
                ProviderStatus.TIMEOUT,
                ProviderStatus.MALFORMED,
            ):
                raw_provider_results[model_name] = {
                    "error": provider_result.status.value
                }
            else:
                # Serialize result for debug output
                serialized = {
                    "status": provider_result.status.value,
                    "labels": [lb.model_dump() for lb in provider_result.labels],
                    "questions": [q.model_dump() for q in provider_result.questions],
                }
                if provider_result.error:
                    serialized["error"] = provider_result.error
                raw_provider_results[model_name] = serialized

                # Aggregate label scores via typed ProviderLabel objects
                for label_entry in provider_result.labels:
                    if label_entry.legal_problem_code in taxonomy_dict:
                        weighted_score = base_weight * label_entry.confidence
                        label_scores[label_entry.legal_problem_code] += weighted_score

                # Collect questions from this provider
                all_questions_by_provider[model_name] = provider_result.questions

        # Normalize label scores to 0-1 range based on max possible score
        # Only count providers that completed successfully
        successful_providers = [
            pr
            for pr in results
            if pr.status in (ProviderStatus.SUCCESS, ProviderStatus.EMPTY)
        ]
        max_possible_score = sum(
            self.model_weights.get(pr.model_name, 1.0) for pr in successful_providers
        )
        if max_possible_score > 0:
            normalized_label_scores = {
                label: score / max_possible_score
                for label, score in label_scores.items()
            }
        else:
            normalized_label_scores = label_scores

        # Sort labels by normalized scores
        sorted_labels = sorted(
            normalized_label_scores.items(), key=lambda item: item[1], reverse=True
        )

        # Collect questions, prioritizing by provider weight.
        # Use (text, format, normalized_options) as unique key so that
        # same/similar text with different format/options survive to the
        # deterministic dedup step below.
        all_question_objs = []
        seen_question_sigs = set()

        for model_name in sorted(
            all_questions_by_provider.keys(),
            key=lambda name: self.model_weights.get(name, 1.0),
            reverse=True,
        ):
            for question_entry in all_questions_by_provider[model_name]:
                norm_fmt = Classifier.Provider._normalize_format(question_entry.format)
                norm_opts_key = Classifier.Provider._normalized_options_key(
                    question_entry.options, norm_fmt
                )
                sig = (
                    question_entry.question.strip().lower(),
                    norm_fmt,
                    norm_opts_key,
                )
                if sig not in seen_question_sigs:
                    seen_question_sigs.add(sig)
                    all_question_objs.append(
                        FollowUpQuestion(
                            question=question_entry.question,
                            format=question_entry.format,
                            options=question_entry.options,
                        )
                    )

        # Merge questions deterministically
        merged_questions = self._deterministic_deduplicate(all_question_objs)
        final_top_questions = merged_questions[:3] if merged_questions else []

        # Debug log all labels that survived voting
        if DEBUG and sorted_labels:
            logger.debug(f"""All voted labels (top {len(sorted_labels)}):""")
            for label, score in sorted_labels:
                original_label = (
                    taxonomy_dict.get(label, label) if taxonomy_dict else label
                )
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

        # Keyword evidence may supplement an LLM classification, but must never
        # determine eligibility without at least one recognized LLM label.
        llm_label_contributed = any(
            pr.model_name in ("gpt-4.1-mini", "gpt-5-nano")
            and any(label.legal_problem_code in taxonomy_dict for label in pr.labels)
            for pr in successful_providers
        )
        if not llm_label_contributed:
            if top_legal_problem_code is not None:
                top_legal_problem_code = None
                top_confidence = None
            is_eligible = None

        # Determine if we should include follow-up questions
        questions_to_include = None
        if not top_legal_problem_code and final_top_questions:
            # No label but questions exist — include them to seek clarification
            questions_to_include = final_top_questions
        elif top_confidence is not None and top_confidence < self.follow_up_threshold:
            questions_to_include = final_top_questions

        clarification = (
            "¿Podría describir su situación legal con más detalle? Por ejemplo, ¿se trata de vivienda, familia, empleo, beneficios, problemas del consumidor, o algo más?"
            if language.strip().lower() == "spanish"
            else "Could you please describe your legal situation in more detail? For example, is it about housing, family, employment, benefits, consumer issues, or something else?"
        )

        # If low confidence and no provider questions, add deterministic clarification
        if (
            top_legal_problem_code is not None
            and top_confidence is not None
            and top_confidence < self.follow_up_threshold
            and not final_top_questions
        ):
            questions_to_include = [
                FollowUpQuestion(question=clarification),
            ]
            final_top_questions = questions_to_include

        # If no label and no questions, provide a deterministic clarification
        if not top_legal_problem_code and not final_top_questions:
            response_data = {
                "follow_up_questions": [
                    FollowUpQuestion(question=clarification),
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
        return ClassificationResponse(**response_data)

    async def classify(
        self,
        problem_description: str,
        language: str = "English",
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
            logger.debug("Starting classification request")

        taxonomy = self.taxonomy
        if not taxonomy:
            return ClassificationResponse(
                follow_up_questions=[
                    FollowUpQuestion(question="Taxonomy could not be loaded.")
                ],
            )

        if not self.providers:
            return ClassificationResponse(
                follow_up_questions=[
                    FollowUpQuestion(
                        question="No providers available for classification."
                    )
                ],
            )

        # Run all providers concurrently with a shared deadline
        tasks: list[tuple[str, asyncio.Task]] = []
        taxonomy_keys = list(taxonomy.keys())
        final_prompt = self.load_prompt(taxonomy_keys)
        for provider in self.providers:
            provider_kwargs = {
                "problem_description": problem_description,
                "prompt": final_prompt,
                "taxonomy": taxonomy_keys,
            }
            if provider.reasoning_effort:
                provider_kwargs["reasoning_effort"] = provider.reasoning_effort
            task = asyncio.create_task(provider.classify(**provider_kwargs))
            tasks.append((provider.model_name, task))

        timeout = Classifier.Provider.PROVIDER_TIMEOUT
        task_set = {t for _, t in tasks}
        done: set[asyncio.Task] = set()
        pending = set(task_set)
        try:
            done, pending = await asyncio.wait(
                task_set,
                timeout=timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
        except BaseException:
            # On cancellation or unexpected error, cancel all unfinished tasks
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise

        result_by_task: dict[asyncio.Task, ProviderResult] = {}

        # Cancel all pending (timed-out) tasks before collecting results.
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # Collect results in provider declaration order.  The completion set is
        # unordered, so iterating it would make equal-weight votes unstable.
        for provider_name, task in tasks:
            if task in pending:
                result_by_task[task] = ProviderResult(
                    model_name=provider_name,
                    status=ProviderStatus.TIMEOUT,
                    error=f"Provider timeout after {timeout}s",
                )
                continue
            if task not in done:
                continue

            try:
                result = task.result()
                if isinstance(result, ProviderResult):
                    result_by_task[task] = result
                else:
                    result_by_task[task] = ProviderResult(
                        model_name=provider_name,
                        status=ProviderStatus.FAILURE,
                        error="Unexpected non-ProviderResult return",
                    )
            except Exception as e:  # noqa: BLE001 - provider tasks fail independently
                result_by_task[task] = ProviderResult(
                    model_name=provider_name,
                    status=ProviderStatus.FAILURE,
                    error=f"{type(e).__name__}",
                )

        results = [result_by_task[task] for _, task in tasks]

        # Always use weighted voting for aggregation
        response = await self._get_voted_results(results, taxonomy, language)

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
        PROVIDER_TIMEOUT = 45.0  # total budget for one provider's retry sequence

        client: AsyncAzureOpenAI | None = None
        reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None

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
        def _parse_retry_after(exc: BaseException) -> float | None:
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
                        return max(
                            1.0,
                            min(float(retry_after), Classifier.Provider.MAX_WAIT_TIME),
                        )
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
                Classifier.Provider.BASE_WAIT_TIME * (2**attempt),
                Classifier.Provider.MAX_WAIT_TIME,
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

        @staticmethod
        def _normalize_format(fmt: Any) -> str | None:
            if not isinstance(fmt, str):
                return None
            return fmt.strip().lower() or None

        @staticmethod
        def _normalize_options(opts: Any) -> list[str] | None:
            if not isinstance(opts, list):
                return None
            normalized = []
            seen = set()
            for o in opts:
                if isinstance(o, str) and o.strip():
                    key = o.strip().lower()
                    if key not in seen:
                        seen.add(key)
                        normalized.append(key)
            return normalized if normalized else None

        @staticmethod
        def _normalized_options_key(
            opts: Any, fmt: str | None = None
        ) -> tuple[str, ...] | None:
            normalized = Classifier.Provider._normalize_options(opts)
            if normalized is None:
                if fmt == "yesno":
                    return ("no", "yes")
                return None
            return tuple(sorted(normalized))

        @staticmethod
        def _validate_and_build_result(model_name: str, parsed: Any) -> ProviderResult:
            """Validate raw parsed JSON and build a ProviderResult with typed
            ProviderLabel/ProviderQuestion models. Returns MALFORMED for
            structurally invalid payloads.  Individual malformed entries
            within labelled lists are skipped (safe typed rejection)."""
            if not isinstance(parsed, dict):
                return ProviderResult(
                    model_name=model_name,
                    status=ProviderStatus.MALFORMED,
                    error="response is not a dict",
                )
            if parsed.get("likely_no_legal_problem"):
                return ProviderResult(
                    model_name=model_name, status=ProviderStatus.EMPTY
                )
            labels: list[ProviderLabel] = []
            questions: list[ProviderQuestion] = []
            # Validate categories (position-based labels)
            categories = parsed.get("categories")
            if categories is not None:
                if not isinstance(categories, list):
                    return ProviderResult(
                        model_name=model_name,
                        status=ProviderStatus.MALFORMED,
                        error="categories not a list",
                    )
                for i, cat in enumerate(categories):
                    if isinstance(cat, str) and cat.strip():
                        pos_conf = [1.0, 0.7, 0.4] + [0.2] * max(0, i - 2)
                        labels.append(
                            ProviderLabel(
                                legal_problem_code=cat.strip(),
                                confidence=pos_conf[i] if i < len(pos_conf) else 0.2,
                            )
                        )
            # Validate labels
            raw_labels = parsed.get("labels")
            if raw_labels is not None:
                if not isinstance(raw_labels, list):
                    return ProviderResult(
                        model_name=model_name,
                        status=ProviderStatus.MALFORMED,
                        error="labels not a list",
                    )
                for item in raw_labels:
                    if isinstance(item, str) and item.strip():
                        labels.append(ProviderLabel(legal_problem_code=item.strip()))
                    elif isinstance(item, dict):
                        code = item.get("label") or item.get("legal_problem_code")
                        if isinstance(code, str) and code.strip():
                            conf = item.get("confidence", 1.0)
                            if not isinstance(conf, (int, float)):
                                continue
                            labels.append(
                                ProviderLabel(
                                    legal_problem_code=code.strip(),
                                    confidence=float(conf),
                                )
                            )
            # Validate questions (both followup_questions and questions keys)
            for key in ("followup_questions", "questions"):
                raw_qs = parsed.get(key)
                if raw_qs is None:
                    continue
                if not isinstance(raw_qs, list):
                    return ProviderResult(
                        model_name=model_name,
                        status=ProviderStatus.MALFORMED,
                        error=f"{key} not a list",
                    )
                for q in raw_qs:
                    if isinstance(q, dict):
                        q_text = q.get("question")
                        if isinstance(q_text, str) and q_text.strip():
                            fmt = Classifier.Provider._normalize_format(
                                q.get("format") or q.get("type")
                            )
                            opts = Classifier.Provider._normalize_options(
                                q.get("options")
                            )
                            questions.append(
                                ProviderQuestion(
                                    question=q_text.strip(),
                                    format=fmt,
                                    options=opts,
                                )
                            )
                    elif isinstance(q, str) and q.strip():
                        questions.append(ProviderQuestion(question=q.strip()))
            if not labels and not questions:
                return ProviderResult(
                    model_name=model_name, status=ProviderStatus.EMPTY
                )
            return ProviderResult(
                model_name=model_name,
                status=ProviderStatus.SUCCESS,
                labels=labels,
                questions=questions,
            )

        async def classify(
            self,
            problem_description: str,
            prompt: str,
            reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None,
            **kwargs,
        ) -> ProviderResult:
            """Common classification logic for OpenAI-compatible clients.

            Uses self.client which should be an OpenAI-compatible async client instance.

            Args:
              problem_description: The problem description to classify.
              prompt: The prompt template to use.
              reasoning_effort: Reasoning effort level for gpt-5 models. Can be "minimal", "low", "medium", or "high".
              **kwargs: Additional arguments (ignored).

            Returns:
              A ProviderResult envelope.
            """
            try:

                async def _make_request():
                    if DEBUG:
                        start_time = time.time()
                        logger.debug(f"""[{self.model_name}] Starting API call""")

                    # Build request parameters
                    request_params = {
                        "model": getattr(self, "deployment_name", self.model_name),
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
                        except Exception as e:  # noqa: BLE001 - signature introspection is optional
                            logger.debug(
                                f"[{self.model_name}] Could not inspect client signature: {e}"
                            )

                    response = await self.client.chat.completions.create(
                        **request_params
                    )

                    if DEBUG:
                        elapsed = time.time() - start_time
                        logger.debug(
                            f"""[{self.model_name}] API call completed in {elapsed:.2f}s"""
                        )

                    return response

                response = await self._call_with_retry_async(_make_request)
                content = response.choices[0].message.content
                parsed_response = json.loads(content)
                return self._validate_and_build_result(self.model_name, parsed_response)

            except json.JSONDecodeError as e:
                return ProviderResult(
                    model_name=self.model_name,
                    status=ProviderStatus.MALFORMED,
                    error=str(e),
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001 - provider failures become result data
                return ProviderResult(
                    model_name=self.model_name,
                    status=ProviderStatus.FAILURE,
                    error=f"{type(e).__name__}",
                )

    class AzureOpenAIProvider(Provider):
        def __init__(self, model_name: str = "gpt-5-nano"):
            """Initialize Azure OpenAI provider.

            Args:
              model_name: Logical model identifier used for weighting/config.
            """
            super().__init__(model_name)
            self.deployment_name = self._resolve_deployment_name(model_name)
            self.client = AsyncAzureOpenAI(
                api_key=require_ev("AZURE_API_KEY"),
                api_version="2025-03-01-preview",
                azure_endpoint=require_ev("AZURE_LLM_ENDPOINT"),
            )
            if model_name.startswith("gpt-5"):
                self.reasoning_effort = "minimal"

        @staticmethod
        def _resolve_deployment_name(model_name: str) -> str:
            env_suffix = re.sub(r"[^A-Za-z0-9]+", "_", model_name).upper()
            return (
                os.getenv(f"AZURE_CLASSIFIER_{env_suffix}_DEPLOYMENT")
                or os.getenv("AZURE_LLM_MODEL")
                or model_name
            )

    class KeywordProvider(Provider):
        """Simple keyword-based provider."""

        def __init__(self):
            """Initialize keyword provider."""
            super().__init__("keyword")

        async def classify(
            self, problem_description: str, taxonomy: list[str], **kwargs
        ) -> ProviderResult:
            """Classify using fuzzy keyword matching with rapidfuzz.

            Combines fuzzy string matching with direct word matching to find
            relevant legal categories from the problem description.
            """
            if taxonomy is None:
                return ProviderResult(
                    model_name=self.model_name,
                    status=ProviderStatus.EMPTY,
                )
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
                "being",
                "be",
                "he",
                "she",
                "we",
                "they",
            }

            words = problem_description.lower().split()
            key_terms = {
                w.rstrip(".,!?;:()[]{}").lower()
                for w in words
                if len(w) > 3
                and w.rstrip(".,!?;:()[]{}").lower() not in words_to_ignore
            }
            key_phrase = " ".join(
                sorted(key_terms)
            )  # All key terms for stronger signal

            if not key_phrase.strip():
                key_phrase = (
                    problem_description  # Fall back to full description if no key terms
                )

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
                # Normalize non-alphanumeric boundaries so "landlord" matches "Private Landlord/Tenant"
                category_words = re.sub(r"[^a-z0-9]+", " ", category_lower)
                direct_match_bonus = 0
                for term in key_terms:
                    # Check for whole word matches (not substring)
                    if (
                        f""" {term} """ in f""" {category_words} """
                        or category_words.startswith(term + " ")
                        or category_words.endswith(f""" {term}""")
                    ):
                        direct_match_bonus += 5  # +5% for each direct word match

                # Only accept matches that either:
                # 1. Have direct word matches (direct_match_bonus > 0)
                # 2. Have high fuzzy match score (> 65%)
                if direct_match_bonus > 0 or score > 65:
                    final_score = min(100, score + direct_match_bonus)
                    filtered_labels[category] = final_score / 100.0

            # Sort by score and return top matches as dicts
            sorted_matches = sorted(
                filtered_labels.items(), key=lambda x: x[1], reverse=True
            )
            labels = [
                ProviderLabel(legal_problem_code=label, confidence=conf)
                for label, conf in sorted_matches
            ]
            logger.debug(f"""[KeywordProvider] {labels}""")
            status = ProviderStatus.EMPTY if not labels else ProviderStatus.SUCCESS
            return ProviderResult(
                model_name=self.model_name,
                status=status,
                labels=labels,
            )


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
