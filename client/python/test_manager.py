"""
Comprehensive test validation and analysis tool.

Combines validation, result management, and detailed analysis into a single package.

Usage:
    # Show summary of all tests
    python test_manager.py

    # Show detailed results for all tests
    python test_manager.py view --detailed

    # Show only failed tests
    python test_manager.py view --failed

    # Show only passed tests
    python test_manager.py view --passed

    # Show details for a specific call
    python test_manager.py view <call_id>

    # Validate a specific call
    python test_manager.py validate <call_id> <script_name>

    # Custom paths
    python test_manager.py -f /path/to/client_test_results.json view --detailed
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from rapidfuzz import fuzz, process, utils


class StateValidator:
    """Validates actual state against expected state and generates test results."""

    # Keys that are automatically added by the system and should be ignored during validation
    SYSTEM_KEYS = {"_state_saved", "call_id", "status", "error", "case_description"}

    def __init__(self):
        self.mismatches: List[Dict[str, Any]] = []

    def _fuzzy_match_key(self, key: str, candidates: List[str], threshold: int = 80) -> str | None:
        """
        Use fuzzy matching to find a close match for a key (typically for names).

        Uses token_set_ratio for better matching of multi-word names.
        - token_set_ratio finds best token-level match, ideal for name variations
        - preprocessing normalizes case and whitespace
        - threshold of 80 provides good balance between flexibility and accuracy

        Args:
            key: The key to match (e.g., "Victoria Rodriguez")
            candidates: List of candidate keys to match against (e.g., ["Victoria Lynn Rodriguez", "Sophie", ...])
            threshold: Minimum match ratio (0-100, default 80)

        Returns:
            The best matching key or None if no match found above threshold
        """
        if not candidates:
            return None

        # Use token_set_ratio for better name matching
        # token_set_ratio finds the best matching at the token level, which works well for name variations
        # (e.g., "Victoria Rodriguez" matches "Victoria Lynn Rodriguez" at 100%)
        best_match = process.extractOne(
            key,
            list(candidates),
            scorer=fuzz.token_set_ratio,
            processor=utils.default_process,
            score_cutoff=threshold,
        )

        return best_match[0] if best_match else None

    def compare_states(
        self, actual_state: Dict[str, Any], expected_state: Dict[str, Any]
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Compare actual state with expected state recursively.

        Returns:
            Tuple of (all_match: bool, mismatches: List[Dict])
        """
        self.mismatches = []
        self._compare_dict(actual_state, expected_state)
        return len(self.mismatches) == 0, self.mismatches

    def _compare_dict(self, actual: Dict[str, Any], expected: Dict[str, Any], path: str = ""):
        """Recursively compare dictionaries."""
        # Track which actual keys have been matched (to detect truly extra keys later)
        matched_actual_keys = set()

        # Check for missing keys in actual
        for key in expected:
            new_path = f"{path}.{key}" if path else key
            # Try exact match first, then case-insensitive match
            matching_key = None
            if key in actual:
                matching_key = key
            else:
                # Try case-insensitive match
                for actual_key in actual:
                    if isinstance(key, str) and isinstance(actual_key, str):
                        if key.lower() == actual_key.lower():
                            matching_key = actual_key
                            break

            if matching_key is None:
                # For income.listing and assets.listing, try fuzzy matching on string keys
                if ("income.listing" in path or "assets.listing" in path) and isinstance(key, str):
                    # Use threshold of 50 for generous matching (e.g., "savings account" vs "account")
                    threshold = 50 if "assets.listing" in path else 75
                    fuzzy_match = self._fuzzy_match_key(
                        key, list(actual.keys()), threshold=threshold
                    )
                    if fuzzy_match:
                        matching_key = fuzzy_match
                        matched_actual_keys.add(fuzzy_match)
                        # Use the actual matched key in the path, not the expected key
                        fuzzy_new_path = f"{path}.{fuzzy_match}" if path else fuzzy_match
                        self._compare_values(actual[fuzzy_match], expected[key], fuzzy_new_path)
                    else:
                        self.mismatches.append(
                            {
                                "path": new_path,
                                "issue": "missing_key",
                                "expected": expected[key],
                                "actual": None,
                            }
                        )
                # Also try numeric key matching (e.g., 100198 vs "100198")
                elif ("income.listing" in path or "assets.listing" in path) and isinstance(
                    key, int
                ):
                    str_key = str(key)
                    if str_key in actual:
                        matching_key = str_key
                        matched_actual_keys.add(str_key)
                        self._compare_values(actual[str_key], expected[key], new_path)
                    else:
                        self.mismatches.append(
                            {
                                "path": new_path,
                                "issue": "missing_key",
                                "expected": expected[key],
                                "actual": None,
                            }
                        )
                else:
                    self.mismatches.append(
                        {
                            "path": new_path,
                            "issue": "missing_key",
                            "expected": expected[key],
                            "actual": None,
                        }
                    )
            else:
                matched_actual_keys.add(matching_key)
                self._compare_values(actual[matching_key], expected[key], new_path)

        # Check for extra keys in actual (but ignore system keys)
        for key in actual:
            # Skip keys that were already matched to expected keys
            if key in matched_actual_keys:
                continue

            if key in self.SYSTEM_KEYS:
                continue

            new_path = f"{path}.{key}" if path else key
            self.mismatches.append(
                {
                    "path": new_path,
                    "issue": "extra_key",
                    "expected": None,
                    "actual": actual[key],
                }
            )

    def _compare_values(self, actual: Any, expected: Any, path: str):
        """Recursively compare values."""
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                self.mismatches.append(
                    {
                        "path": path,
                        "issue": "type_mismatch",
                        "expected_type": type(expected).__name__,
                        "actual_type": type(actual).__name__,
                        "expected": expected,
                        "actual": actual,
                    }
                )
            else:
                self._compare_dict(actual, expected, path)

        elif isinstance(expected, list):
            if not isinstance(actual, list):
                self.mismatches.append(
                    {
                        "path": path,
                        "issue": "type_mismatch",
                        "expected_type": "list",
                        "actual_type": type(actual).__name__,
                        "expected": expected,
                        "actual": actual,
                    }
                )
            else:
                self._compare_list(actual, expected, path)

        else:
            # For string comparisons, normalize to lowercase
            if isinstance(actual, str) and isinstance(expected, str):
                # Use fuzzy matching for address fields (character-level matching)
                if "address" in path:
                    match_ratio = fuzz.ratio(actual.lower(), expected.lower())
                    # 90% threshold catches real differences (wrong streets) but allows minor spacing/punctuation variations
                    if match_ratio < 90:
                        self.mismatches.append(
                            {
                                "path": path,
                                "issue": "value_mismatch",
                                "expected": expected,
                                "actual": actual,
                                "match_ratio": match_ratio,
                            }
                        )
                elif actual.lower() != expected.lower():
                    self.mismatches.append(
                        {
                            "path": path,
                            "issue": "value_mismatch",
                            "expected": expected,
                            "actual": actual,
                        }
                    )
            elif actual != expected:
                self.mismatches.append(
                    {
                        "path": path,
                        "issue": "value_mismatch",
                        "expected": expected,
                        "actual": actual,
                    }
                )

    def _compare_list(self, actual: List[Any], expected: List[Any], path: str):
        """Recursively compare lists."""
        if len(actual) != len(expected):
            self.mismatches.append(
                {
                    "path": path,
                    "issue": "list_length_mismatch",
                    "expected_length": len(expected),
                    "actual_length": len(actual),
                    "expected": expected,
                    "actual": actual,
                }
            )
            return

        for i, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            new_path = f"{path}[{i}]"
            self._compare_values(actual_item, expected_item, new_path)


class TestResultManager:
    """Manages test results and persists them to a JSON file."""

    def __init__(self, results_file: str = "client_test_results.json"):
        self.results_file = results_file
        self.results: Dict[str, Any] = self._load_results()

    def _load_results(self) -> Dict[str, Any]:
        """Load existing test results if file exists."""
        if Path(self.results_file).exists():
            try:
                with open(self.results_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def add_test_result(
        self,
        call_id: str,
        script_name: str,
        passed: bool,
        mismatches: List[Dict[str, Any]] = None,
        state_file: str = "logs/flow_manager_state.json",
    ):
        """Add a test result for a specific call."""
        self.results[call_id] = {
            "script": script_name,
            "passed": passed,
            "mismatch_count": len(mismatches) if mismatches else 0,
            "mismatches": mismatches or [],
            "state_file": state_file,
        }

    def save_results(self):
        """Save test results to JSON file."""
        with open(self.results_file, "w") as f:
            json.dump(self.results, f, indent=2)

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of test results."""
        if not self.results:
            return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0}

        total = len(self.results)
        passed = sum(1 for r in self.results.values() if r["passed"])
        failed = total - passed

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": (passed / total * 100) if total > 0 else 0.0,
        }


class TestRunner:
    """Main test runner and analyzer."""

    def __init__(
        self,
        results_file: str = "client_test_results.json",
        flow_manager_state_file: str = "flow_manager_state.json",
        scripts_file: str = None,
    ):
        self.results_file = results_file
        self.flow_manager_state_file = flow_manager_state_file
        self.result_manager = TestResultManager(results_file)

        # Load scripts
        if scripts_file is None:
            scripts_file = Path(__file__).parent / "scripts.yml"
        else:
            scripts_file = Path(scripts_file)

        if not scripts_file.exists():
            print(f"Error: Scripts file not found: {scripts_file}")
            sys.exit(1)

        with open(scripts_file) as f:
            self.scripts = yaml.safe_load(f) or {}

        # Load flow manager state
        try:
            with open(flow_manager_state_file, "r") as f:
                self.flow_manager_state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.flow_manager_state = {}

    async def revalidate_all(self) -> Dict[str, Tuple[bool, List[Dict[str, Any]]]]:
        """
        Revalidate all existing test results, but only for call_ids that have state data.
        Tests without state data are removed from results.

        Returns:
            Dictionary mapping call_id to (passed: bool, mismatches: List[Dict])
        """
        revalidation_results = {}

        if not self.result_manager.results:
            print("No existing test results to revalidate")
            return revalidation_results

        # Store the initial set of call_ids to revalidate
        call_ids = list(self.result_manager.results.keys())
        print(f"Revalidating {len(call_ids)} tests...\n")

        # Track which call_ids to remove (no state data)
        call_ids_to_remove = []

        for call_id in call_ids:
            # Skip if this call_id doesn't have state data
            if call_id not in self.flow_manager_state:
                call_ids_to_remove.append(call_id)
                print(f"⊘ Skipping {call_id}: no state data available")
                continue

            result = self.result_manager.results.get(call_id)
            if not result:
                print(f"⚠ Skipping {call_id}: not found in results")
                continue

            script_name = result.get("script")
            if not script_name:
                print(f"⚠ Skipping {call_id}: no script name found")
                continue

            try:
                passed, mismatches = await self.validate_call(call_id, script_name)
                revalidation_results[call_id] = (passed, mismatches)
                status = "✓" if passed else "✗"
                mismatch_count = len(mismatches)
                print(
                    f"{status} {call_id}: {script_name} "
                    f"({mismatch_count} mismatch{'es' if mismatch_count != 1 else ''})"
                )
            except Exception as e:
                print(f"ERROR {call_id}: Error during revalidation - {str(e)}")
                revalidation_results[call_id] = (False, [{"error": str(e)}])

        # Remove call_ids without state data from results
        for call_id in call_ids_to_remove:
            if call_id in self.result_manager.results:
                del self.result_manager.results[call_id]

        # Save the cleaned results
        if call_ids_to_remove:
            self.result_manager.save_results()

        return revalidation_results

    async def validate_call(
        self,
        call_id: str,
        script_name: str,
        expected_state: Dict[str, Any] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Validate a specific call.

        Args:
            call_id: The call ID to validate
            script_name: The script name (used to look up expected_state if not provided)
            expected_state: Optional expected state (if not provided, loaded from scripts)

        Returns:
            Tuple of (passed: bool, mismatches: List[Dict])
        """
        # Load expected state from scripts if not provided
        if expected_state is None:
            if script_name not in self.scripts:
                error_mismatches = [
                    {
                        "path": "root",
                        "issue": "script_not_found",
                        "message": f"Script '{script_name}' not found in scripts.yml",
                    }
                ]
                self.result_manager.add_test_result(call_id, script_name, False, error_mismatches)
                self.result_manager.save_results()
                return False, error_mismatches

            script_config = self.scripts[script_name]
            if isinstance(script_config, str):
                error_mismatches = [
                    {
                        "path": "root",
                        "issue": "invalid_script_format",
                        "message": f"Script '{script_name}' is in old string format, no expected_state available",
                    }
                ]
                self.result_manager.add_test_result(call_id, script_name, False, error_mismatches)
                self.result_manager.save_results()
                return False, error_mismatches

            if "expected_state" not in script_config:
                error_mismatches = [
                    {
                        "path": "root",
                        "issue": "missing_expected_state",
                        "message": f"Script '{script_name}' does not have 'expected_state' defined",
                    }
                ]
                self.result_manager.add_test_result(call_id, script_name, False, error_mismatches)
                self.result_manager.save_results()
                return False, error_mismatches

            expected_state = script_config["expected_state"]

        # Load actual state - at this point we know the call_id exists because
        # revalidate_all skips call_ids not in flow_manager_state
        if call_id not in self.flow_manager_state:
            # This should not happen during revalidate, but handle it for validate command
            error_mismatches = [
                {
                    "path": "root",
                    "issue": "call_id_not_found",
                    "message": f"call_id {call_id} not found in {self.flow_manager_state_file}",
                }
            ]
            self.result_manager.add_test_result(call_id, script_name, False, error_mismatches)
            self.result_manager.save_results()
            return False, error_mismatches

        actual_state = self.flow_manager_state[call_id]

        # Compare states
        validator = StateValidator()
        passed, mismatches = validator.compare_states(actual_state, expected_state)

        # Save test results
        self.result_manager.add_test_result(call_id, script_name, passed, mismatches)
        self.result_manager.save_results()

        return passed, mismatches

    def print_summary(self):
        """Print summary of all test results."""
        if not self.result_manager.results:
            print("No test results found")
            return

        summary = self.result_manager.get_summary()
        total = summary["total"]
        passed = summary["passed"]
        failed = summary["failed"]
        pass_rate = summary["pass_rate"]

        print(f"\n{'=' * 60}")
        print("TEST RESULTS SUMMARY")
        print(f"{'=' * 60}")
        print(f"Total Tests:    {total}")
        print(f"Passed:         {passed}")
        print(f"Failed:         {failed}")
        print(f"Pass Rate:      {pass_rate:.1f}%")
        print(f"{'=' * 60}\n")

        # Group by script
        by_script = {}
        for call_id, result in self.result_manager.results.items():
            script = result.get("script", "unknown")
            if script not in by_script:
                by_script[script] = {"passed": 0, "failed": 0}
            if result["passed"]:
                by_script[script]["passed"] += 1
            else:
                by_script[script]["failed"] += 1

        print("Results by Script:")
        for script in sorted(by_script.keys()):
            stats = by_script[script]
            total_for_script = stats["passed"] + stats["failed"]
            rate = (stats["passed"] / total_for_script * 100) if total_for_script > 0 else 0
            status = "✓ PASS" if stats["failed"] == 0 else "✗ FAIL"
            print(f"  {status} {script:40} {stats['passed']}/{total_for_script} ({rate:.0f}%)")

        print("\nTest Details:")
        for call_id, result in sorted(self.result_manager.results.items()):
            status = "✓" if result["passed"] else "✗"
            script = result.get("script", "unknown")
            print(f"  {status} {script:20} {call_id}")
        print()

    def print_detailed(self, show_passed: bool = True, show_failed: bool = True):
        """Print detailed results for all tests."""
        if not self.result_manager.results:
            print("No test results found")
            return

        print(f"\n{'=' * 80}")
        print("DETAILED TEST RESULTS")
        print(f"{'=' * 80}\n")

        for call_id, result in sorted(self.result_manager.results.items()):
            if result["passed"] and not show_passed:
                continue
            if not result["passed"] and not show_failed:
                continue

            status = "✓ PASS" if result["passed"] else "✗ FAIL"
            script = result.get("script", "unknown")
            mismatches = result.get("mismatches", [])
            mismatch_count = result.get("mismatch_count", 0)

            print(f"{status} {script}")
            print(f"    Call ID: {call_id}")
            if not result["passed"]:
                print(f"    Mismatches: {mismatch_count}")
                for i, mismatch in enumerate(mismatches[:5], 1):
                    path = mismatch.get("path", "unknown")
                    issue = mismatch.get("issue", "unknown")
                    expected = mismatch.get("expected", "N/A")
                    actual = mismatch.get("actual", "N/A")
                    print(f"      {i}. {path}")
                    print(f"         Issue: {issue}")
                    print(f"         Expected: {expected}")
                    print(f"         Actual: {actual}")
                if len(mismatches) > 5:
                    print(f"      ... and {len(mismatches) - 5} more mismatches")
            print()

    def print_call_details(self, call_id: str):
        """Print detailed information about a specific call."""
        if call_id not in self.result_manager.results:
            print(f"Call ID '{call_id}' not found in results")
            return False

        result = self.result_manager.results[call_id]

        print(f"\n{'=' * 80}")
        print(f"CALL DETAILS: {call_id}")
        print(f"{'=' * 80}\n")

        status = "✓ PASS" if result["passed"] else "✗ FAIL"
        print(f"Status:       {status}")
        print(f"Script:       {result.get('script', 'unknown')}")
        print(f"Mismatches:   {result.get('mismatch_count', 0)}")
        print()

        mismatches = result.get("mismatches", [])
        if mismatches:
            print("Mismatch Details:")
            print("-" * 80)
            for i, mismatch in enumerate(mismatches, 1):
                print(f"\n{i}. Path: {mismatch.get('path', 'unknown')}")
                print(f"   Issue: {mismatch.get('issue', 'unknown')}")

                if "value_mismatch" in mismatch.get("issue", ""):
                    print(f"   Expected: {mismatch.get('expected', 'N/A')}")
                    print(f"   Actual:   {mismatch.get('actual', 'N/A')}")
                elif "type_mismatch" in mismatch.get("issue", ""):
                    print(f"   Expected Type: {mismatch.get('expected_type', 'N/A')}")
                    print(f"   Actual Type:   {mismatch.get('actual_type', 'N/A')}")
                    print(f"   Expected: {mismatch.get('expected', 'N/A')}")
                    print(f"   Actual:   {mismatch.get('actual', 'N/A')}")
                elif "list_length_mismatch" in mismatch.get("issue", ""):
                    print(f"   Expected Length: {mismatch.get('expected_length', 'N/A')}")
                    print(f"   Actual Length:   {mismatch.get('actual_length', 'N/A')}")
                else:
                    for key, value in mismatch.items():
                        if key not in ["path", "issue"]:
                            print(f"   {key}: {value}")
        else:
            print("No mismatches - call passed validation!")
        print()
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive test validation and analysis tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show summary of all tests
  python test_manager.py

  # Show detailed results
  python test_manager.py view --detailed

  # Show only failed tests
  python test_manager.py view --failed

  # Show details for specific call
  python test_manager.py view <call_id>

  # Validate a specific call
  python test_manager.py validate <call_id> <script_name>

  # Revalidate all existing test results
  python test_manager.py revalidate

  # Revalidate and show summary only
  python test_manager.py revalidate --summary

  # Custom result file location
  python test_manager.py -f logs/client_test_results.json view --detailed
        """,
    )

    # Global arguments (apply to all commands)
    parser.add_argument(
        "-f",
        "--file",
        type=str,
        default="logs/client_test_results.json",
        help="path to test results file (default: logs/client_test_results.json)",
    )
    parser.add_argument(
        "--state-file",
        type=str,
        default="logs/flow_manager_state.json",
        help="path to flow_manager_state.json (default: logs/flow_manager_state.json)",
    )
    parser.add_argument(
        "--scripts-file",
        type=str,
        default=None,
        help="path to scripts.yml (default: scripts.yml in same directory)",
    )

    # Create subparsers for commands
    subparsers = parser.add_subparsers(dest="command", help="command to run")

    # Validate subcommand
    validate_parser = subparsers.add_parser("validate", help="validate a specific call")
    validate_parser.add_argument("call_id", help="call ID to validate")
    validate_parser.add_argument("script_name", help="script name for the call")

    # Revalidate subcommand
    revalidate_parser = subparsers.add_parser(
        "revalidate", help="revalidate all existing test results"
    )
    revalidate_parser.add_argument(
        "--summary",
        action="store_true",
        help="show summary only, not detailed results",
    )

    # View/analysis commands (add as a pseudo-subcommand using parent parser)
    view_parser = subparsers.add_parser("view", help="view test results")
    view_parser.add_argument(
        "call_id",
        nargs="?",
        help="specific call ID to view details",
    )
    view_parser.add_argument(
        "--detailed",
        action="store_true",
        help="show detailed results for all tests",
    )
    view_parser.add_argument(
        "--failed",
        action="store_true",
        help="show only failed tests",
    )
    view_parser.add_argument(
        "--passed",
        action="store_true",
        help="show only passed tests",
    )

    args = parser.parse_args()

    # Initialize test runner
    runner = TestRunner(
        results_file=args.file,
        flow_manager_state_file=args.state_file,
        scripts_file=args.scripts_file,
    )

    # Handle validate command
    if args.command == "validate":
        passed, mismatches = __import__("asyncio").run(
            runner.validate_call(args.call_id, args.script_name)
        )
        # Reload results to get the newly saved test result
        runner.result_manager.results = runner.result_manager._load_results()
        runner.print_call_details(args.call_id)
        return

    # Handle revalidate command
    if args.command == "revalidate":
        __import__("asyncio").run(runner.revalidate_all())
        # Rebuild the in-memory results to match what was just validated
        # (validate_call saves to disk but we need in-memory state to match)
        runner.result_manager.results = runner.result_manager._load_results()
        if hasattr(args, "summary") and args.summary:
            runner.print_summary()
        else:
            runner.print_summary()
            runner.print_detailed(show_passed=True, show_failed=True)
        return

    # Handle view command or default (show summary)
    if args.command == "view" or args.command is None:
        if hasattr(args, "call_id") and args.call_id:
            # Show details for specific call
            runner.print_call_details(args.call_id)
        elif hasattr(args, "detailed") and args.detailed:
            # Show detailed results
            show_passed = not (hasattr(args, "failed") and args.failed)
            show_failed = not (hasattr(args, "passed") and args.passed)
            runner.print_summary()
            runner.print_detailed(show_passed=show_passed, show_failed=show_failed)
        elif hasattr(args, "failed") and args.failed:
            # Show only failed tests
            runner.print_summary()
            runner.print_detailed(show_passed=False, show_failed=True)
        elif hasattr(args, "passed") and args.passed:
            # Show only passed tests
            runner.print_summary()
            runner.print_detailed(show_passed=True, show_failed=False)
        else:
            # Default: show summary
            runner.print_summary()


if __name__ == "__main__":
    main()
