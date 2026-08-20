import asyncio
from unittest.mock import patch

import aiohttp
import pytest

from intake_bot.models.legalserver import (
    LegalServerOverall,
    MatterLookupOutcome,
    MatterLookupResult,
    OperationKind,
    OperationOutcome,
    RecordResult,
)
from intake_bot.services.legalserver import (
    _build_matter_payload,
    _ChildCollectionCache,
    _collect_fallback_content,
    _create_matter_guarded,
    _find_matter_by_external_id,
    _now,
    _post_fallback_note,
    _post_once,
    _save_additional_names,
    _save_adverse_parties,
    _save_case_description_note,
    _save_income_records,
    _save_rejection_note,
    save_intake_legalserver,
)


def _far_deadline() -> float:
    return _now() + 3600


class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self._now_val = start

    def now(self) -> float:
        return self._now_val

    async def sleep(self, seconds: float) -> None:
        self._now_val += seconds


@pytest.fixture(autouse=True)
def _enable_legalserver_connection_by_default(monkeypatch):
    monkeypatch.setenv("LEGALSERVER_TESTING_DISABLE_CONNECTION", "false")
    monkeypatch.setenv("LEGAL_SERVER_SUBDOMAIN", "test-subdomain")
    monkeypatch.setenv("LEGAL_SERVER_BEARER_TOKEN", "test-token")


class _FakeResponse:
    def __init__(
        self,
        status=200,
        json_data=None,
        text_data="",
        headers=None,
        *,
        json_was_set=False,
    ):
        self.status = status
        self._json_data = json_data if json_was_set or json_data is not None else {}
        self._text_data = text_data
        self.headers = headers or {}
        self.released = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):
        return self._json_data

    async def text(self):
        return self._text_data

    def release(self):
        self.released = True


class _FakeRequestContextManager:
    def __init__(self, response):
        self._response = response

    @property
    def status(self):
        return self._response.status

    async def json(self, *args, **kwargs):
        return await self._response.json(*args, **kwargs)

    def __await__(self):
        return self._response.__await__()

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def release(self):
        self._response.release()


class _FakeClientSession:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.default_response = _FakeResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        key = (method, url)
        if key in self.responses:
            resp = self.responses[key]
        elif method in self.responses:
            resp = self.responses[method]
        else:
            resp = self.default_response
        if isinstance(resp, (list, tuple)):
            if not resp:
                resp = self.default_response
            else:
                resp = resp[0]
                if key in self.responses and isinstance(self.responses[key], list):
                    self.responses[key] = self.responses[key][1:]
                elif method in self.responses and isinstance(
                    self.responses[method], list
                ):
                    self.responses[method] = self.responses[method][1:]
        if isinstance(resp, Exception):
            raise resp
        return _FakeRequestContextManager(resp)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    async def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


class TestModuleImport:
    def test_import_succeeds_without_credentials(self, monkeypatch):
        monkeypatch.delenv("LEGAL_SERVER_SUBDOMAIN", raising=False)
        monkeypatch.delenv("LEGAL_SERVER_BEARER_TOKEN", raising=False)
        import importlib

        import intake_bot.services.legalserver as ls

        importlib.reload(ls)
        assert ls is not None

    def test_lazy_api_base_url_raises_without_subdomain(self, monkeypatch):
        monkeypatch.delenv("LEGAL_SERVER_SUBDOMAIN", raising=False)
        monkeypatch.delenv("LEGAL_SERVER_BEARER_TOKEN", raising=False)
        import importlib

        import intake_bot.services.legalserver as ls

        importlib.reload(ls)
        with pytest.raises(ValueError, match="LEGAL_SERVER_SUBDOMAIN"):
            ls._legalserver_api_base_url()

    def test_lazy_headers_raises_without_bearer_token(self, monkeypatch):
        monkeypatch.delenv("LEGAL_SERVER_SUBDOMAIN", raising=False)
        monkeypatch.delenv("LEGAL_SERVER_BEARER_TOKEN", raising=False)
        import importlib

        import intake_bot.services.legalserver as ls

        importlib.reload(ls)
        with pytest.raises(ValueError, match="LEGAL_SERVER_BEARER_TOKEN"):
            ls._legalserver_headers()

    @pytest.mark.asyncio
    async def test_save_intake_legalserver_returns_skipped_when_disabled(
        self, monkeypatch
    ):
        monkeypatch.delenv("LEGAL_SERVER_SUBDOMAIN", raising=False)
        monkeypatch.delenv("LEGAL_SERVER_BEARER_TOKEN", raising=False)
        monkeypatch.setenv("LEGALSERVER_TESTING_DISABLE_CONNECTION", "true")
        import importlib

        import intake_bot.services.legalserver as ls

        importlib.reload(ls)
        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            result = await ls.save_intake_legalserver({})
            assert result.overall == ls.LegalServerOverall.SKIPPED
            assert result.matter_uuid is None
            mock_logger.debug.assert_called_with("LegalServer connection disabled")


class TestLookupByIdResponseLifecycle:
    @pytest.mark.asyncio
    async def test_lookup_response_released_when_match_returns(self, monkeypatch):
        response = _FakeResponse(
            status=200,
            json_data={"data": [{"id": 7, "name": "Example"}]},
        )
        session = _FakeClientSession({"GET": response})
        monkeypatch.setattr(
            "intake_bot.services.legalserver.aiohttp.ClientSession",
            lambda **kwargs: session,
        )

        from intake_bot.services.legalserver import find_lookup_by_id

        result = await find_lookup_by_id(7)

        assert result["lookup_value"]["id"] == 7
        assert response.released is True

    @pytest.mark.asyncio
    async def test_lookup_response_released_on_non_success(self, monkeypatch):
        response = _FakeResponse(status=404)
        session = _FakeClientSession({"GET": response})
        monkeypatch.setattr(
            "intake_bot.services.legalserver.aiohttp.ClientSession",
            lambda **kwargs: session,
        )

        from intake_bot.services.legalserver import find_lookup_by_id

        assert await find_lookup_by_id(7) is None
        assert response.released is True


class TestBuildMatterPayload:
    def test_basic_payload_with_required_fields(self):
        state = {
            "names": {
                "names": [
                    {
                        "first": "John",
                        "last": "Doe",
                        "middle": "Michael",
                        "suffix": None,
                    }
                ]
            }
        }
        payload = _build_matter_payload(state)
        assert payload["first"] == "John"
        assert payload["last"] == "Doe"
        assert payload["middle"] == "Michael"
        assert payload["case_disposition"] == "Rejected"
        assert payload["rejection_reason"]["lookup_value_name"] == "Other"
        assert "suffix" not in payload

    def test_payload_with_phone_number(self):
        state = {
            "names": {"names": [{"first": "Jane", "last": "Smith"}]},
            "phone": {"is_valid": True, "phone_number": "(866) 534-5243"},
        }
        payload = _build_matter_payload(state)
        assert payload["mobile_phone"] == "(866) 534-5243"

    def test_payload_sets_client_legal_name_custom_field(self):
        state = {
            "names": {
                "names": [{"first": "Jane", "last": "Smith", "type": "Legal Name"}]
            }
        }
        payload = _build_matter_payload(state)
        assert payload["custom_fields"]["is_this_the_client_s_legal_name__1065"] is True

    def test_payload_with_legal_problem_code(self):
        state = {
            "names": {"names": [{"first": "Alice", "last": "Johnson"}]},
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "32 Divorce/Sep./Annul.",
            },
        }
        payload = _build_matter_payload(state)
        assert payload["legal_problem_code"] == "32 Divorce/Sep./Annul."

    def test_payload_with_county_of_dispute(self):
        state = {
            "names": {"names": [{"first": "Bob", "last": "Wilson"}]},
            "service_area": {
                "location": "Amelia County",
                "is_eligible": True,
                "fips_code": 51007,
            },
        }
        payload = _build_matter_payload(state)
        assert payload["county_of_dispute"] == {"county_FIPS": "51007"}

    def test_payload_with_income_eligibility(self):
        state = {
            "names": {"names": [{"first": "Carol", "last": "Brown"}]},
            "income": {
                "is_eligible": True,
                "monthly_amount": 2000,
                "household_size": 3,
            },
        }
        payload = _build_matter_payload(state)
        assert payload["income_eligible"] is True
        assert payload["number_of_adults"] == 3

    def test_payload_excludes_none_values(self):
        state = {"names": {"names": [{"first": "Iris", "last": "Kim", "middle": None}]}}
        payload = _build_matter_payload(state)
        assert "middle" not in payload

    def test_payload_with_missing_names_section(self):
        assert _build_matter_payload({}) is None

    def test_payload_with_empty_names_list(self):
        assert _build_matter_payload({"names": {"names": []}}) is None


class TestMatterLookupResult:
    """Typed lookup FOUND / NOT_FOUND / INDETERMINATE."""

    @pytest.mark.asyncio
    async def test_lookup_not_found(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(status=200, json_data={"data": []}),
            }
        )
        result = await _find_matter_by_external_id(session, "ext-1", _far_deadline())
        assert result.result == MatterLookupResult.NOT_FOUND
        assert result.matter_uuid is None
        assert session.calls[0]["kwargs"]["params"]["results"] == "full"

    @pytest.mark.asyncio
    async def test_restricted_empty_page_is_indeterminate(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(
                    status=200,
                    json_data={
                        "data": [],
                        "total_records": 1,
                        "authorized_records": 0,
                    },
                )
            }
        )

        result = await _find_matter_by_external_id(session, "ext-1", _far_deadline())

        assert result.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_lookup_found_returns_uuid(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(
                    status=200,
                    json_data={
                        "data": [{"external_id": "ext-1", "matter_uuid": "m-1"}]
                    },
                ),
            }
        )
        result = await _find_matter_by_external_id(session, "ext-1", _far_deadline())
        assert result.result == MatterLookupResult.FOUND
        assert result.matter_uuid == "m-1"

    @pytest.mark.asyncio
    async def test_lookup_indeterminate_on_error(self):
        clock = _FakeClock()
        session = _FakeClientSession({"GET": _FakeResponse(status=500)})
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            result = await _find_matter_by_external_id(
                session, "ext-1", clock.now() + 10
            )
        assert result.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_lookup_indeterminate_on_4xx(self):
        session = _FakeClientSession({"GET": _FakeResponse(status=403)})
        result = await _find_matter_by_external_id(session, "ext-1", _far_deadline())
        assert result.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_lookup_indeterminate_on_timeout(self):
        clock = _FakeClock(start=1000.0)
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            session = _FakeClientSession({"GET": aiohttp.ClientError("timeout")})
            result = await _find_matter_by_external_id(
                session, "ext-1", clock.now() + 30.0
            )
            assert result.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_transient_lookup_retries_beyond_three_attempts(self):
        clock = _FakeClock()
        session = _FakeClientSession(
            {
                "GET": [
                    _FakeResponse(503),
                    _FakeResponse(503),
                    _FakeResponse(503),
                    _FakeResponse(200, {"data": []}),
                ]
            }
        )
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            result = await _find_matter_by_external_id(
                session, "ext-1", clock.now() + 100
            )
        assert result.result == MatterLookupResult.NOT_FOUND
        assert len(session.calls) == 4

    @pytest.mark.asyncio
    async def test_deterministic_4xx_lookup_does_not_retry(self):
        session = _FakeClientSession(
            {"GET": [_FakeResponse(404), _FakeResponse(200, {"data": []})]}
        )
        result = await _find_matter_by_external_id(session, "ext-1", _far_deadline())
        assert result.result == MatterLookupResult.INDETERMINATE
        assert len(session.calls) == 1

    @pytest.mark.asyncio
    async def test_lookup_requires_matching_external_id(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(
                    status=200,
                    json_data={"data": [{"matter_uuid": "unverified"}]},
                )
            }
        )

        result = await _find_matter_by_external_id(session, "ext-1", _far_deadline())

        assert result.result == MatterLookupResult.INDETERMINATE
        assert result.matter_uuid is None


class TestMatterCreationIdempotency:
    """Ambiguous create then lookup recovery; no duplicate POST."""

    @pytest.mark.asyncio
    async def test_ambiguous_post_then_lookup_recovers(self):
        """503 -> lookup recovers -> UUID returned, exactly one POST."""
        clock = _FakeClock(start=1000.0)
        session = _FakeClientSession({"POST": _FakeResponse(status=503, json_data={})})
        payload = {"first": "A", "last": "B"}

        async def _fake_lookup_first(sess, ext_id, deadline):
            return MatterLookupOutcome(MatterLookupResult.NOT_FOUND)

        async def _fake_lookup_second(sess, ext_id, deadline):
            return MatterLookupOutcome(MatterLookupResult.FOUND, "recovered-uuid")

        lookup_results = [_fake_lookup_first, _fake_lookup_second]

        async def _fake_lookup(sess, ext_id, deadline):
            fn = lookup_results.pop(0)
            return await fn(sess, ext_id, deadline)

        with (
            patch(
                "intake_bot.services.legalserver._find_matter_by_external_id",
                _fake_lookup,
            ),
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            result = await _create_matter_guarded(
                session, payload, "ext-123", clock.now() + 30.0
            )
            assert result == "recovered-uuid"
            post_calls = [c for c in session.calls if c["method"] == "POST"]
            assert len(post_calls) == 1

    @pytest.mark.asyncio
    async def test_ambiguous_post_then_indeterminate_relookup_stops(self):
        """503 -> relookup INDETERMINATE -> no more POST, returns None."""
        clock = _FakeClock(start=1000.0)
        session = _FakeClientSession({"POST": _FakeResponse(status=503, json_data={})})
        payload = {"first": "A", "last": "B"}

        call_count = 0

        async def _fake_lookup(sess, ext_id, deadline):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MatterLookupOutcome(MatterLookupResult.NOT_FOUND)
            return MatterLookupOutcome(MatterLookupResult.INDETERMINATE)

        with (
            patch(
                "intake_bot.services.legalserver._find_matter_by_external_id",
                _fake_lookup,
            ),
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            result = await _create_matter_guarded(
                session, payload, "ext-123", clock.now() + 30.0
            )
            assert result is None
            post_calls = [c for c in session.calls if c["method"] == "POST"]
            assert len(post_calls) == 1

    @pytest.mark.asyncio
    async def test_409_relookup_recovers(self):
        """409 -> lookup recovers -> UUID returned, exactly one POST."""
        clock = _FakeClock(start=1000.0)
        session = _FakeClientSession({"POST": _FakeResponse(status=409, json_data={})})
        payload = {"first": "A", "last": "B"}

        call_count = 0

        async def _fake_lookup(sess, ext_id, deadline):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MatterLookupOutcome(MatterLookupResult.NOT_FOUND)
            return MatterLookupOutcome(MatterLookupResult.FOUND, "existing")

        with (
            patch(
                "intake_bot.services.legalserver._find_matter_by_external_id",
                _fake_lookup,
            ),
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            result = await _create_matter_guarded(
                session, payload, "ext-123", clock.now() + 30.0
            )
            assert result == "existing"
            post_calls = [c for c in session.calls if c["method"] == "POST"]
            assert len(post_calls) == 1

    @pytest.mark.asyncio
    async def test_2xx_without_uuid_polls_without_reposting(self):
        """201 without UUID is reconciled without duplicating the create."""
        clock = _FakeClock(start=1000.0)
        session = _FakeClientSession(
            {"POST": _FakeResponse(status=201, json_data={"data": {}})}
        )
        payload = {"first": "A", "last": "B"}

        call_count = 0

        async def _fake_lookup(sess, ext_id, deadline):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return MatterLookupOutcome(MatterLookupResult.NOT_FOUND)
            return MatterLookupOutcome(MatterLookupResult.FOUND, "recovered")

        with (
            patch(
                "intake_bot.services.legalserver._find_matter_by_external_id",
                _fake_lookup,
            ),
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            result = await _create_matter_guarded(
                session, payload, "ext-123", clock.now() + 30.0
            )

        assert result == "recovered"
        assert len([c for c in session.calls if c["method"] == "POST"]) == 1

    @pytest.mark.asyncio
    async def test_initial_lookup_found_skips_create(self):
        """Matter exists -> skip create entirely."""
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(
                    status=200,
                    json_data={
                        "data": [{"external_id": "ext-123", "matter_uuid": "existing"}]
                    },
                ),
            }
        )
        payload = {"first": "A", "last": "B"}
        result = await _create_matter_guarded(
            session, payload, "ext-123", _far_deadline()
        )
        assert result == "existing"
        assert len([c for c in session.calls if c["method"] == "POST"]) == 0


class TestChildReconciliation:
    @pytest.mark.asyncio
    async def test_existing_records_are_recovered_without_post(self):
        session = _FakeClientSession(
            {
                (
                    "GET",
                    "https://test-subdomain.legalserver.org/api/v2/matters/m-1/incomes",
                ): _FakeResponse(
                    200,
                    {
                        "data": [
                            {
                                "amount": 50000,
                                "period": "Annually",
                                "type": {"lookup_value_name": "Employment"},
                            }
                        ]
                    },
                ),
                (
                    "POST",
                    "https://test-subdomain.legalserver.org/api/v2/matters/m-1/incomes",
                ): _FakeResponse(201, {}),
            }
        )
        income_data = {
            "listing": {
                "John": {"Employment": {"amount": 50000, "period": "Annually"}},
                "Jane": {"Other": {"amount": 30000, "period": "Monthly"}},
            }
        }
        results = await _save_income_records(
            session, "m-1", income_data, _far_deadline()
        )
        assert len(results) == 2
        assert results[0].outcome == OperationOutcome.RECOVERED
        assert results[1].outcome == OperationOutcome.SUCCESS
        assert len([c for c in session.calls if c["method"] == "POST"]) == 1

    @pytest.mark.asyncio
    async def test_transient_child_post_relist_found_is_recovered(self):
        clock = _FakeClock(start=1000.0)
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            session = _FakeClientSession(
                {
                    "GET": [
                        _FakeResponse(200, {"data": []}),
                        _FakeResponse(
                            200,
                            {
                                "data": [
                                    {
                                        "amount": 50000,
                                        "period": "Annually",
                                        "type": {"lookup_value_name": "Employment"},
                                    }
                                ]
                            },
                        ),
                    ],
                    "POST": _FakeResponse(503, {}),
                }
            )
            income_data = {
                "listing": {
                    "John": {"Employment": {"amount": 50000, "period": "Annually"}},
                }
            }
            results = await _save_income_records(
                session, "m-1", income_data, clock.now() + 5.0
            )
            assert len(results) == 1
            assert results[0].outcome == OperationOutcome.RECOVERED
            assert len([c for c in session.calls if c["method"] == "POST"]) == 1

    @pytest.mark.asyncio
    async def test_transient_child_post_relist_absent_retries_and_succeeds(self):
        clock = _FakeClock(start=1000.0)
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            session = _FakeClientSession(
                {
                    "GET": [
                        _FakeResponse(200, {"data": []}),
                        _FakeResponse(200, {"data": []}),
                    ],
                    "POST": [_FakeResponse(503, {}), _FakeResponse(201, {})],
                }
            )
            results = await _save_income_records(
                session,
                "m-1",
                {
                    "listing": {
                        "John": {"Employment": {"amount": 50000, "period": "Annually"}}
                    }
                },
                clock.now() + 30,
            )
        assert results[0].outcome == OperationOutcome.SUCCESS
        assert len([c for c in session.calls if c["method"] == "POST"]) == 2

    @pytest.mark.asyncio
    async def test_indeterminate_child_list_does_not_post(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, None, json_was_set=True),
                "POST": _FakeResponse(201, {}),
            }
        )
        results = await _save_income_records(
            session,
            "m-1",
            {
                "listing": {
                    "John": {"Employment": {"amount": 50000, "period": "Annually"}}
                }
            },
            _far_deadline(),
        )
        assert results[0].outcome == OperationOutcome.AMBIGUOUS
        assert not [c for c in session.calls if c["method"] == "POST"]

    @pytest.mark.asyncio
    async def test_deterministic_child_4xx_fails_immediately(self):
        session = _FakeClientSession(
            {"GET": _FakeResponse(200, {"data": []}), "POST": _FakeResponse(400, {})}
        )
        results = await _save_income_records(
            session,
            "m-1",
            {
                "listing": {
                    "John": {"Employment": {"amount": 50000, "period": "Annually"}}
                }
            },
            _far_deadline(),
        )
        assert results[0].outcome == OperationOutcome.FAILED
        assert len([c for c in session.calls if c["method"] == "POST"]) == 1

    @pytest.mark.asyncio
    async def test_child_collection_pagination(self):
        url = "https://test-subdomain.legalserver.org/api/v2/matters/m-1/incomes"
        session = _FakeClientSession(
            {
                ("GET", url): [
                    _FakeResponse(
                        200,
                        {
                            "data": [
                                {
                                    "amount": 1,
                                    "period": "Monthly",
                                    "type": {"lookup_value_name": "A"},
                                }
                            ],
                            "total_number_of_pages": 2,
                        },
                    ),
                    _FakeResponse(
                        200,
                        {
                            "data": [
                                {
                                    "amount": 2,
                                    "period": "Monthly",
                                    "type": {"lookup_value_name": "B"},
                                }
                            ],
                            "total_number_of_pages": 2,
                        },
                    ),
                ]
            }
        )
        results = await _save_income_records(
            session,
            "m-1",
            {"listing": {"J": {"C": {"amount": 3, "period": "Monthly"}}}},
            _far_deadline(),
        )
        assert results[0].outcome == OperationOutcome.SUCCESS
        assert len([c for c in session.calls if c["method"] == "GET"]) == 2
        assert [
            c["kwargs"]["params"]["page_number"]
            for c in session.calls
            if c["method"] == "GET"
        ] == [1, 2]

    @pytest.mark.asyncio
    async def test_repeated_final_child_page_is_indeterminate(self):
        page = {
            "data": [
                {
                    "amount": 1,
                    "period": "Monthly",
                    "type": {"lookup_value_name": "A"},
                }
            ],
            "total_number_of_pages": 2,
        }
        url = "https://test-subdomain.legalserver.org/api/v2/matters/m-1/incomes"
        session = _FakeClientSession(
            {
                ("GET", url): [
                    _FakeResponse(200, page),
                    _FakeResponse(200, page),
                ]
            }
        )

        results = await _save_income_records(
            session,
            "m-1",
            {"listing": {"J": {"C": {"amount": 3, "period": "Monthly"}}}},
            _far_deadline(),
        )

        assert results[0].outcome == OperationOutcome.AMBIGUOUS
        assert not [c for c in session.calls if c["method"] == "POST"]

    @pytest.mark.asyncio
    async def test_update_and_update_data_payloads_are_documented(self):
        session = _FakeClientSession(
            {"GET": _FakeResponse(200, {"data": []}), "POST": _FakeResponse(201, {})}
        )
        await _save_income_records(
            session,
            "m-1",
            {"listing": {"J": {"Employment": {"amount": 3, "period": "Monthly"}}}},
            _far_deadline(),
        )
        payload = next(
            c["kwargs"]["json"] for c in session.calls if c["method"] == "POST"
        )
        assert payload["update"] == {
            "amount": 3,
            "period": 12,
            "type": "Employment",
        }
        assert payload["update_data"] == {
            "amount": "3",
            "exclude": False,
            "period": "Monthly",
            "type": {"lookup_value_name": "Employment"},
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "resource,save",
        [
            (
                "additional_names",
                lambda s, d: _save_additional_names(s, "m-1", d, _far_deadline()),
            ),
            (
                "adverse_parties",
                lambda s, d: _save_adverse_parties(s, "m-1", d, _far_deadline()),
            ),
        ],
    )
    async def test_existing_alias_and_adverse_party_recovered_without_post(
        self, resource, save
    ):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(
                    200,
                    {
                        "data": [
                            {
                                "first": "X",
                                "last": "Y",
                                "type": {"lookup_value_name": "Former Name"},
                            }
                        ]
                    },
                )
            }
        )
        data = (
            [{"first": "A", "last": "B"}, {"first": "X", "last": "Y"}]
            if resource == "additional_names"
            else {"adverse_parties": [{"first": "X", "last": "Y"}]}
        )
        result = await save(session, data)
        assert result[0].outcome == OperationOutcome.RECOVERED
        assert not [c for c in session.calls if c["method"] == "POST"]

    @pytest.mark.asyncio
    async def test_organization_adverse_party_uses_organization_name(self):
        session = _FakeClientSession(
            {"GET": _FakeResponse(200, {"data": []}), "POST": _FakeResponse(201, {})}
        )

        result = await _save_adverse_parties(
            session,
            "m-1",
            {"adverse_parties": [{"organization_name": "First National Bank"}]},
            _far_deadline(),
        )

        assert result[0].outcome == OperationOutcome.SUCCESS
        payload = next(
            call["kwargs"]["json"] for call in session.calls if call["method"] == "POST"
        )
        assert payload["update_data"]["organization_name"] == "First National Bank"
        assert "first" not in payload["update_data"]
        assert "last" not in payload["update_data"]

    @pytest.mark.asyncio
    async def test_one_existing_plus_one_missing_posts_only_missing(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(
                    200,
                    {
                        "data": [
                            {
                                "first": "X",
                                "last": "Y",
                                "type": {"lookup_value_name": "Former Name"},
                            }
                        ]
                    },
                ),
                "POST": _FakeResponse(201, {}),
            }
        )
        results = await _save_additional_names(
            session,
            "m-1",
            [
                {"first": "A", "last": "B"},
                {"first": "X", "last": "Y"},
                {"first": "Z", "last": "Q"},
            ],
            _far_deadline(),
        )
        assert [r.outcome for r in results] == [
            OperationOutcome.RECOVERED,
            OperationOutcome.SUCCESS,
        ]
        assert len([c for c in session.calls if c["method"] == "POST"]) == 1

    @pytest.mark.asyncio
    async def test_duplicate_desired_records_use_multiset_and_disable_unsafe_upsert(
        self,
    ):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": [_FakeResponse(400), _FakeResponse(400)],
            }
        )
        results = await _save_income_records(
            session,
            "m-1",
            {
                "listing": {
                    "J": {"Employment": {"amount": 3, "period": "Monthly"}},
                    "K": {"Employment": {"amount": 3, "period": "Monthly"}},
                }
            },
            _far_deadline(),
        )
        assert [r.outcome for r in results] == [
            OperationOutcome.FAILED,
            OperationOutcome.FAILED,
        ]
        assert all(
            "update" not in c["kwargs"]["json"]
            for c in session.calls
            if c["method"] == "POST"
        )

    @pytest.mark.asyncio
    async def test_duplicate_aliases_disable_unsafe_upsert(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": [_FakeResponse(201), _FakeResponse(201)],
            }
        )
        names = [
            {"first": "Primary", "last": "Person"},
            {"first": "Same", "last": "Alias"},
            {"first": "Same", "last": "Alias"},
        ]

        results = await _save_additional_names(session, "m-1", names, _far_deadline())

        assert [result.outcome for result in results] == [
            OperationOutcome.SUCCESS,
            OperationOutcome.SUCCESS,
        ]
        posts = [call for call in session.calls if call["method"] == "POST"]
        assert len(posts) == 2
        assert all("update" not in call["kwargs"]["json"] for call in posts)

    @pytest.mark.asyncio
    async def test_alias_selector_does_not_overwrite_record_with_extra_middle_name(
        self,
    ):
        existing = [
            {
                "first": "Same",
                "middle": "Existing",
                "last": "Alias",
                "type": {"lookup_value_name": "Former Name"},
            }
        ]
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": existing}),
                "POST": _FakeResponse(201),
            }
        )

        results = await _save_additional_names(
            session,
            "m-1",
            [
                {"first": "Primary", "last": "Person"},
                {"first": "Same", "last": "Alias"},
            ],
            _far_deadline(),
        )

        assert results[0].outcome == OperationOutcome.SUCCESS
        post_payload = next(
            call["kwargs"]["json"] for call in session.calls if call["method"] == "POST"
        )
        assert "update" not in post_payload

    @pytest.mark.asyncio
    async def test_inactive_note_is_not_treated_as_recovered(self):
        existing = [
            {
                "subject": "Case Description",
                "body": "same",
                "note_type": {"lookup_value_name": "General Notes"},
                "active": False,
            }
        ]
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": existing}),
                "POST": _FakeResponse(201),
            }
        )

        result = await _save_case_description_note(
            session, "m-1", {"case_description": "same"}, _far_deadline()
        )

        assert result.outcome == OperationOutcome.SUCCESS
        post_payload = next(
            call["kwargs"]["json"] for call in session.calls if call["method"] == "POST"
        )
        assert post_payload["active"] is True
        assert post_payload["update"]["active"] is True

    @pytest.mark.asyncio
    async def test_changed_case_description_updates_stable_note(self):
        existing = [
            {
                "subject": "Case Description",
                "body": "old description",
                "note_type": {"lookup_value_name": "General Notes"},
                "active": True,
            }
        ]
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": existing}),
                "POST": _FakeResponse(201),
            }
        )

        result = await _save_case_description_note(
            session,
            "m-1",
            {"case_description": "new description"},
            _far_deadline(),
        )

        assert result.outcome == OperationOutcome.SUCCESS
        post_payload = next(
            call["kwargs"]["json"] for call in session.calls if call["method"] == "POST"
        )
        assert post_payload["update_data"]["body"] == "new description"

    @pytest.mark.asyncio
    async def test_changed_fallback_updates_stable_note(self):
        existing = [
            {
                "subject": "Unsaved intake sections",
                "body": "Old unresolved content",
                "note_type": {"lookup_value_name": "General Notes"},
                "active": True,
            }
        ]
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": existing}),
                "POST": _FakeResponse(201),
            }
        )

        saved = await _post_fallback_note(
            session, "m-1", ["New unresolved content"], _far_deadline()
        )

        assert saved is True
        post_payload = next(
            call["kwargs"]["json"] for call in session.calls if call["method"] == "POST"
        )
        assert post_payload["update"] == {
            "subject": "Unsaved intake sections",
            "note_type": "General Notes",
        }
        assert post_payload["update_data"]["body"] == "New unresolved content"

    @pytest.mark.asyncio
    async def test_income_fallback_preserves_household_member_name(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": _FakeResponse(400),
            }
        )

        results = await _save_income_records(
            session,
            "m-1",
            {
                "listing": {
                    "Household Member": {
                        "Employment": {"amount": 3, "period": "Monthly"}
                    }
                }
            },
            _far_deadline(),
        )

        assert "Household Member - Employment" in results[0]._fallback_content

    @pytest.mark.asyncio
    async def test_notes_are_idempotent_and_share_cache_across_note_types(self):
        existing = [
            {
                "subject": "Case Description",
                "body": "same",
                "note_type": {"lookup_value_name": "General Notes"},
            }
        ]
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": existing}),
                "POST": _FakeResponse(201, {}),
            }
        )
        deadline = _far_deadline()
        cache = _ChildCollectionCache(session, "m-1", deadline)
        first = await _save_case_description_note(
            session, "m-1", {"case_description": "same"}, deadline, cache
        )
        second = await _save_rejection_note(session, "m-1", "reason", deadline, cache)
        assert first.outcome == OperationOutcome.RECOVERED
        assert second.outcome == OperationOutcome.SUCCESS
        assert len([c for c in session.calls if c["method"] == "GET"]) == 1

    @pytest.mark.asyncio
    async def test_no_pii_is_logged(self):
        sentinel = "SSN-SENTINEL-9999"
        session = _FakeClientSession(
            {"GET": _FakeResponse(200, None, json_was_set=True)}
        )
        with patch("intake_bot.services.legalserver.logger") as logger:
            await _save_income_records(
                session,
                "m-1",
                {
                    "listing": {
                        sentinel: {"Employment": {"amount": 3, "period": "Monthly"}}
                    }
                },
                _far_deadline(),
            )
            assert sentinel not in str(logger.method_calls)

    @pytest.mark.asyncio
    async def test_continue_other_sections_after_failure(self):
        """After income section failure, still attempt other sections."""
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": _FakeResponse(status=400, json_data={}),
            }
        )
        income_data = {
            "listing": {"John": {"Employment": {"amount": 50000, "period": "Annually"}}}
        }
        income_rr = await _save_income_records(
            session, "m-1", income_data, _far_deadline()
        )
        assert income_rr[0].outcome == OperationOutcome.FAILED

        case_rr = await _save_case_description_note(
            session, "m-1", {"case_description": "desc"}, _far_deadline()
        )
        assert case_rr.outcome == OperationOutcome.FAILED


class TestChildRecordResults:
    """Per-record typed operation results."""

    def test_record_result_dataclass(self):
        r = RecordResult(OperationKind.INCOME, OperationOutcome.SUCCESS)
        assert r.kind == OperationKind.INCOME
        assert r.outcome == OperationOutcome.SUCCESS

    def test_record_result_all_outcomes(self):
        assert OperationOutcome.SUCCESS.value == "success"
        assert OperationOutcome.FAILED.value == "failed"
        assert OperationOutcome.AMBIGUOUS.value == "ambiguous"
        assert OperationOutcome.SKIPPED.value == "skipped"
        assert OperationOutcome.RECOVERED.value == "recovered"
        assert OperationOutcome.FALLBACK_PRESERVED.value == "fallback_preserved"

    def test_operation_kind_values(self):
        assert OperationKind.MATTER_CREATE.value == "matter_create"
        assert OperationKind.INCOME.value == "income"
        assert OperationKind.ALIAS.value == "alias"
        assert OperationKind.ADVERSE_PARTY.value == "adverse_party"
        assert OperationKind.CASE_DESCRIPTION.value == "case_description"
        assert OperationKind.ASSETS.value == "assets"
        assert OperationKind.REJECTION_REASON.value == "rejection_reason"
        assert OperationKind.FALLBACK_NOTE.value == "fallback_note"


class TestDeadlineBound:
    """Deadline expiration stops requests and caps sleeps."""

    @pytest.mark.asyncio
    async def test_post_once_exhausted_deadline(self):
        clock = _FakeClock(start=1000.0)
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            session = _FakeClientSession({"POST": _FakeResponse(status=200)})
            result, ambiguous = await _post_once(
                session,
                "POST",
                "/test",
                json={},
                deadline=clock.now() - 1.0,
            )
            assert result is None
            assert ambiguous is True

    @pytest.mark.asyncio
    async def test_income_skip_after_deadline(self):
        clock = _FakeClock(start=1000.0)
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            session = _FakeClientSession({"POST": _FakeResponse(status=201)})
            income_data = {
                "listing": {
                    "John": {"Employment": {"amount": 50000, "period": "Annually"}}
                }
            }
            results = await _save_income_records(
                session, "m-1", income_data, clock.now() - 1.0
            )
            assert len(results) == 1
            assert results[0].outcome == OperationOutcome.AMBIGUOUS


class TestConsolidatedFallback:
    """One consolidated fallback note with corrected extraction."""

    @pytest.mark.asyncio
    async def test_consolidated_note_accepted(self):
        """Single POST, successful 2xx yields True."""
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": _FakeResponse(status=201, json_data={}),
            }
        )
        ok = await _post_fallback_note(
            session, "m-1", ["Section: data"], _far_deadline()
        )
        assert ok is True
        assert len(session.calls) == 2

    @pytest.mark.asyncio
    async def test_consolidated_note_ambiguous_fails(self):
        """503 from fallback POST yields False (ambiguous)."""
        clock = _FakeClock()
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": _FakeResponse(status=503, json_data={}),
            }
        )
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
        ):
            ok = await _post_fallback_note(
                session, "m-1", ["Section: data"], clock.now() + 10
            )
        assert ok is False

    @pytest.mark.asyncio
    async def test_empty_content_returns_false(self):
        session = _FakeClientSession()
        ok = await _post_fallback_note(session, "m-1", [], _far_deadline())
        assert ok is False
        assert len(session.calls) == 0

    @pytest.mark.asyncio
    async def test_fallback_failure_returns_false(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": _FakeResponse(status=400, json_data={}),
            }
        )
        ok = await _post_fallback_note(
            session, "m-1", ["Income: data"], _far_deadline()
        )
        assert ok is False


class TestOverallOutcomes:
    """COMPLETE, DEGRADED, FAILED, SKIPPED."""

    @pytest.mark.asyncio
    async def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("LEGALSERVER_TESTING_DISABLE_CONNECTION", "true")
        result = await save_intake_legalserver({})
        assert result.overall == LegalServerOverall.SKIPPED
        assert result.matter_uuid is None

    @pytest.mark.asyncio
    async def test_failed_no_name(self):
        result = await save_intake_legalserver({"call_id": "c1"})
        assert result.overall == LegalServerOverall.FAILED

    @pytest.mark.asyncio
    async def test_complete(self, monkeypatch):
        class _OkSession:
            def __init__(self, *a, **kw):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def request(self, method, url, **kwargs):
                self.calls.append({"method": method, "url": url})
                if any(
                    resource in url
                    for resource in (
                        "/notes",
                        "/incomes",
                        "/additional_names",
                        "/adverse_parties",
                    )
                ):
                    resp = (
                        _FakeResponse(status=200, json_data={"data": []})
                        if method == "GET"
                        else _FakeResponse(status=201, json_data={})
                    )
                elif "/matters" in url and method == "POST":
                    resp = _FakeResponse(
                        status=201, json_data={"data": {"matter_uuid": "m-1"}}
                    )
                elif "/matters" in url and method == "GET":
                    resp = _FakeResponse(status=200, json_data={"data": []})
                else:
                    resp = _FakeResponse(status=201, json_data={})
                return _FakeRequestContextManager(resp)

            def get(self, url, **kw):
                return self.request("GET", url, **kw)

            def post(self, url, **kw):
                return self.request("POST", url, **kw)

        monkeypatch.setattr(aiohttp, "ClientSession", _OkSession)
        result = await save_intake_legalserver(
            {
                "call_id": "c1",
                "names": {"names": [{"first": "A", "last": "B"}]},
            }
        )
        assert result.overall == LegalServerOverall.COMPLETE

    @pytest.mark.asyncio
    async def test_degraded_with_fallback(self, monkeypatch):
        class _DegradedSession:
            def __init__(self, *a, **kw):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def request(self, method, url, **kwargs):
                self.calls.append({"method": method, "url": url})
                if "/incomes" in url:
                    resp = (
                        _FakeResponse(status=200, json_data={"data": []})
                        if method == "GET"
                        else _FakeResponse(status=400, json_data={})
                    )
                elif "/notes" in url:
                    resp = (
                        _FakeResponse(status=200, json_data={"data": []})
                        if method == "GET"
                        else _FakeResponse(status=201, json_data={})
                    )
                elif "/additional_names" in url or "/adverse_parties" in url:
                    resp = _FakeResponse(status=201, json_data={})
                elif "/matters" in url and method == "POST":
                    resp = _FakeResponse(
                        status=201, json_data={"data": {"matter_uuid": "m-1"}}
                    )
                elif "/matters" in url and method == "GET":
                    resp = _FakeResponse(status=200, json_data={"data": []})
                else:
                    resp = _FakeResponse(status=201, json_data={})
                return _FakeRequestContextManager(resp)

            def get(self, url, **kw):
                return self.request("GET", url, **kw)

            def post(self, url, **kw):
                return self.request("POST", url, **kw)

        monkeypatch.setattr(aiohttp, "ClientSession", _DegradedSession)
        result = await save_intake_legalserver(
            {
                "call_id": "c1",
                "names": {"names": [{"first": "A", "last": "B"}]},
                "income": {
                    "listing": {
                        "John": {"Employment": {"amount": 50000, "period": "Annually"}}
                    }
                },
            }
        )
        assert result.overall == LegalServerOverall.DEGRADED

    @pytest.mark.asyncio
    async def test_has_operations_field(self):
        result = await save_intake_legalserver({})
        assert hasattr(result, "operations")

    @pytest.mark.asyncio
    async def test_result_serializable(self):
        result = LegalServerOverall.COMPLETE
        assert result.value == "complete"
        assert result == LegalServerOverall.COMPLETE

    @pytest.mark.asyncio
    async def test_save_propagates_cancellation(self, monkeypatch):
        from intake_bot.services import legalserver

        monkeypatch.setattr(
            "intake_bot.services.legalserver._build_matter_payload",
            lambda state: {"first": "A", "last": "B", "rejected": False},
        )

        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(
            "intake_bot.services.legalserver._create_matter_guarded", cancelled
        )

        class _Session:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        monkeypatch.setattr(
            "intake_bot.services.legalserver.aiohttp.ClientSession", _Session
        )

        with pytest.raises(asyncio.CancelledError):
            await legalserver.save_intake_legalserver(
                {"call_id": "c1", "names": {"names": [{"first": "A", "last": "B"}]}}
            )

    @pytest.mark.asyncio
    async def test_save_propagates_keyboard_interrupt(self, monkeypatch):
        from intake_bot.services import legalserver

        monkeypatch.setattr(
            "intake_bot.services.legalserver._build_matter_payload",
            lambda state: {"first": "A", "last": "B", "rejected": False},
        )

        async def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(
            "intake_bot.services.legalserver._create_matter_guarded", interrupted
        )

        class _Session:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        monkeypatch.setattr(
            "intake_bot.services.legalserver.aiohttp.ClientSession", _Session
        )

        with pytest.raises(KeyboardInterrupt):
            await legalserver.save_intake_legalserver(
                {"call_id": "c1", "names": {"names": [{"first": "A", "last": "B"}]}}
            )


class TestFallbackNoteFailure:
    """Fallback failure produces FAILED overall."""

    @pytest.mark.asyncio
    async def test_fallback_failure_produces_failed(self, monkeypatch):
        class _FailSession:
            def __init__(self, *a, **kw):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def request(self, method, url, **kwargs):
                self.calls.append({"method": method, "url": url})
                if "/incomes" in url or "/notes" in url:
                    resp = (
                        _FakeResponse(status=200, json_data={"data": []})
                        if method == "GET"
                        else _FakeResponse(status=400, json_data={})
                    )
                elif "/additional_names" in url or "/adverse_parties" in url:
                    resp = _FakeResponse(status=201, json_data={})
                elif "/matters" in url and method == "POST":
                    resp = _FakeResponse(
                        status=201, json_data={"data": {"matter_uuid": "m-1"}}
                    )
                elif "/matters" in url and method == "GET":
                    resp = _FakeResponse(status=200, json_data={"data": []})
                else:
                    resp = _FakeResponse(status=201, json_data={})
                return _FakeRequestContextManager(resp)

            def get(self, url, **kw):
                return self.request("GET", url, **kw)

            def post(self, url, **kw):
                return self.request("POST", url, **kw)

        monkeypatch.setattr(aiohttp, "ClientSession", _FailSession)
        result = await save_intake_legalserver(
            {
                "call_id": "c1",
                "names": {"names": [{"first": "A", "last": "B"}]},
                "income": {
                    "listing": {
                        "John": {"Employment": {"amount": 50000, "period": "Annually"}}
                    }
                },
            }
        )
        assert result.overall == LegalServerOverall.FAILED


class TestCollectFallbackContent:
    @pytest.mark.asyncio
    async def test_collect_fallback_content(self):
        ops = [
            RecordResult(
                OperationKind.INCOME,
                OperationOutcome.FAILED,
                _fallback_content="Employment: 50000",
            ),
            RecordResult(OperationKind.ALIAS, OperationOutcome.SUCCESS),
            RecordResult(
                OperationKind.CASE_DESCRIPTION,
                OperationOutcome.AMBIGUOUS,
                _fallback_content="Landlord dispute",
            ),
        ]
        parts = _collect_fallback_content(ops, None)
        assert any("Employment: 50000" in p for p in parts)
        assert any("Landlord dispute" in p for p in parts)
        assert not any("alias" in p.lower() and "Income" not in p for p in parts)


class TestSaveRejectionNote:
    @pytest.mark.asyncio
    async def test_save_rejection_note_success(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": _FakeResponse(status=201, json_data={}),
            }
        )
        result = await _save_rejection_note(
            session, "m-1", "Over Income", _far_deadline()
        )
        assert result.outcome == OperationOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_save_rejection_note_failure(self):
        session = _FakeClientSession(
            {
                "GET": _FakeResponse(200, {"data": []}),
                "POST": _FakeResponse(status=400, json_data={}),
            }
        )
        result = await _save_rejection_note(
            session, "m-1", "Over Income", _far_deadline()
        )
        assert result.outcome == OperationOutcome.FAILED


# ---------------------------------------------------------------------------
# Restored preexisting payload tests
# ---------------------------------------------------------------------------


class TestRestoredPayload:
    def test_household_composition(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "household_composition": {"number_of_adults": 2, "number_of_children": 3},
        }
        p = _build_matter_payload(state)
        assert p["number_of_adults"] == 2
        assert p["number_of_children"] == 3

    def test_household_overrides_income(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "income": {"is_eligible": True, "household_size": 5},
            "household_composition": {"number_of_adults": 2, "number_of_children": 3},
        }
        p = _build_matter_payload(state)
        assert p["number_of_adults"] == 2
        assert p["number_of_children"] == 3

    def test_asset_eligibility(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "assets": {"is_eligible": False, "total_value": 5000},
        }
        p = _build_matter_payload(state)
        assert p["asset_eligible"] is False

    def test_citizenship_true(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "citizenship": {"is_citizen": True},
        }
        p = _build_matter_payload(state)
        assert p["citizenship"] == "Citizen"

    def test_citizenship_false(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "citizenship": {"is_citizen": False},
        }
        p = _build_matter_payload(state)
        assert p["citizenship"] == "Non-Citizen"

    def test_domestic_violence_true(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "domestic_violence": {"is_experiencing": True},
        }
        p = _build_matter_payload(state)
        assert p["victim_of_domestic_violence"] is True

    def test_domestic_violence_false(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "domestic_violence": {"is_experiencing": False},
        }
        p = _build_matter_payload(state)
        assert p["victim_of_domestic_violence"] is False

    def test_date_of_birth(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "date_of_birth": {"date_of_birth": "1990-05-15"},
        }
        p = _build_matter_payload(state)
        assert p["date_of_birth"] == "1990-05-15"

    def test_dob_missing_section_not_in_payload(self):
        p = _build_matter_payload({"names": {"names": [{"first": "A", "last": "B"}]}})
        assert "date_of_birth" not in p

    def test_ssn_last_4(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "ssn_last_4": {"ssn_last_4": "5678"},
        }
        p = _build_matter_payload(state)
        assert p["ssn"] == "5678"

    def test_address_full(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "address": {
                "address": {
                    "street": "123 Main",
                    "street_2": "Apt 4",
                    "city": "R",
                    "state": "VA",
                    "zip": "23219",
                    "county": "Arlington",
                }
            },
        }
        p = _build_matter_payload(state)
        assert p["home_street"] == "123 Main"
        assert p["home_apt_num"] == "Apt 4"
        assert p["home_city"] == "R"
        assert p["home_state"] == "VA"
        assert p["home_zip"] == "23219"

    def test_county_of_residence(self):
        state = {
            "names": {"names": [{"first": "A", "last": "B"}]},
            "address": {
                "address": {
                    "street": "1",
                    "city": "C",
                    "state": "VA",
                    "zip": "1",
                    "county": "A",
                }
            },
        }
        p = _build_matter_payload(state)
        assert p["county_of_residence"]["county_name"] == "A"
        assert p["county_of_residence"]["county_state"] == "VA"

    def test_complete_payload_date_of_birth(self):
        state = {
            "call_id": "c1",
            "phone": {"phone_number": "(703) 555-1234"},
            "names": {
                "names": [{"first": "S", "middle": "J", "last": "A", "suffix": "Jr."}]
            },
            "date_of_birth": {"date_of_birth": "1975-03-20"},
            "address": {
                "address": {
                    "street": "789 Elm",
                    "street_2": "S100",
                    "city": "Alex",
                    "state": "VA",
                    "zip": "22314",
                    "county": "A",
                }
            },
            "service_area": {"fips_code": 51013},
            "case_type": {"legal_problem_code": "42 Family"},
            "income": {"is_eligible": True, "household_size": 2},
            "assets": {"is_eligible": True, "total_value": 0},
            "citizenship": {"is_citizen": True},
            "domestic_violence": {"is_experiencing": False},
        }
        p = _build_matter_payload(state)
        assert p["first"] == "S" and p["last"] == "A"
        assert p["date_of_birth"] == "1975-03-20"
        assert p["mobile_phone"] == "(703) 555-1234"
        assert p["income_eligible"] is True
        assert p["asset_eligible"] is True
        assert p["citizenship"] == "Citizen"
        assert p["victim_of_domestic_violence"] is False
        assert p["case_disposition"] == "Incomplete Intake"


# ---------------------------------------------------------------------------
# Matter lookup NOT_FOUND only for valid explicitly empty collection
# ---------------------------------------------------------------------------


class TestLookupNotFoundPrecision:
    @pytest.mark.asyncio
    async def test_empty_data_list_is_not_found(self):
        s = _FakeClientSession({"GET": _FakeResponse(200, {"data": []})})
        lo = await _find_matter_by_external_id(s, "e1", _far_deadline())
        assert lo.result == MatterLookupResult.NOT_FOUND

    @pytest.mark.asyncio
    async def test_data_none_is_indeterminate(self):
        s = _FakeClientSession({"GET": _FakeResponse(200, {"data": None})})
        lo = await _find_matter_by_external_id(s, "e1", _far_deadline())
        assert lo.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_data_missing_is_indeterminate(self):
        s = _FakeClientSession({"GET": _FakeResponse(200, {})})
        lo = await _find_matter_by_external_id(s, "e1", _far_deadline())
        assert lo.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_data_wrong_type_is_indeterminate(self):
        s = _FakeClientSession({"GET": _FakeResponse(200, {"data": "not a list"})})
        lo = await _find_matter_by_external_id(s, "e1", _far_deadline())
        assert lo.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_entry_missing_uuid_is_indeterminate(self):
        s = _FakeClientSession({"GET": _FakeResponse(200, {"data": [{"name": "x"}]})})
        lo = await _find_matter_by_external_id(s, "e1", _far_deadline())
        assert lo.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_entry_wrong_type_is_indeterminate(self):
        s = _FakeClientSession({"GET": _FakeResponse(200, {"data": ["string"]})})
        lo = await _find_matter_by_external_id(s, "e1", _far_deadline())
        assert lo.result == MatterLookupResult.INDETERMINATE

    @pytest.mark.asyncio
    async def test_malformed_json_is_indeterminate(self):
        s = _FakeClientSession(
            {"GET": _FakeResponse(200, {"data": [{"matter_uuid": 123}]})}
        )
        lo = await _find_matter_by_external_id(s, "e1", _far_deadline())
        assert lo.result == MatterLookupResult.INDETERMINATE


# ---------------------------------------------------------------------------
# Matter ambiguity parameterized: timeout, client error, 408, 429, 5xx,
# 409, 422, invalid JSON, malformed shapes, 2xx without UUID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,label",
    [
        (408, "408"),
        (429, "429"),
        (500, "500"),
        (502, "502"),
        (503, "503"),
        (504, "504"),
        (409, "409"),
        (422, "422"),
    ],
)
async def test_matter_ambiguous_scenario(status, label):
    """Each ambiguous POST status triggers exactly one POST and a relookup."""
    clock = _FakeClock(start=1000.0)
    session = _FakeClientSession({"POST": _FakeResponse(status=status, json_data={})})
    payload = {"first": "A", "last": "B"}

    call_count = 0

    async def _lk(sess, eid, dl):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return MatterLookupOutcome(MatterLookupResult.NOT_FOUND)
        return MatterLookupOutcome(MatterLookupResult.FOUND, "recovered")

    with (
        patch("intake_bot.services.legalserver._find_matter_by_external_id", _lk),
        patch("intake_bot.services.legalserver._now", clock.now),
        patch("intake_bot.services.legalserver._sleep", clock.sleep),
    ):
        result = await _create_matter_guarded(
            session, payload, "ext-123", clock.now() + 60.0
        )
        assert result == "recovered", f"Failed for {label}"
        post_count = len([c for c in session.calls if c["method"] == "POST"])
        assert post_count == 1, f"Expected 1 POST for {label}, got {post_count}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["invalid_json", "list_body", "scalar_body", "no_uuid"]
)
async def test_matter_ambiguous_parse_outcomes(scenario):
    """Non-dict response, missing UUID, etc are all ambiguous."""
    clock = _FakeClock(start=1000.0)
    if scenario == "invalid_json":

        class _BrokenResp:
            status = 201
            headers = {}  # noqa: RUF012 - lightweight fake response constant

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def json(self, **kw):
                raise ValueError("bad json")

            def release(self):
                pass

        resp = _BrokenResp()
    elif scenario == "list_body":
        resp = _FakeResponse(201, [{"matter_uuid": "m-1"}])
    elif scenario == "scalar_body":
        resp = _FakeResponse(201, "scalar")
    elif scenario == "no_uuid":
        resp = _FakeResponse(201, {"data": {"not_uuid": "x"}})
    else:
        resp = _FakeResponse(201, {})

    session = _FakeClientSession({"POST": resp})
    payload = {"first": "A", "last": "B"}
    call_count = 0

    async def _lk(sess, eid, dl):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return MatterLookupOutcome(MatterLookupResult.NOT_FOUND)
        return MatterLookupOutcome(MatterLookupResult.FOUND, "recovered")

    with (
        patch("intake_bot.services.legalserver._find_matter_by_external_id", _lk),
        patch("intake_bot.services.legalserver._now", clock.now),
        patch("intake_bot.services.legalserver._sleep", clock.sleep),
    ):
        result = await _create_matter_guarded(
            session, payload, "ext-123", clock.now() + 60.0
        )
        assert result == "recovered", f"Failed for {scenario}"
        assert len([c for c in session.calls if c["method"] == "POST"]) == 1


# ---------------------------------------------------------------------------
# Deadline boundary: post_once and child save ops respect deadline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_once_deadline_after_response():
    """A response received at the boundary is still an authoritative success."""
    clock = _FakeClock(start=1000.0)

    class _LateResp:
        status = 200
        headers = {}  # noqa: RUF012 - lightweight fake response constant

        def __init__(self):
            self._called = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def json(self, **kw):
            return {"ok": True}

        def release(self):
            pass

    resp = _LateResp()

    def _patched_req(method, url, **kw):
        clock._now_val = 2000.0  # advance past deadline
        return _FakeRequestContextManager(resp)

    session = _FakeClientSession()
    session.request = _patched_req
    with (
        patch("intake_bot.services.legalserver._now", clock.now),
        patch("intake_bot.services.legalserver._sleep", clock.sleep),
    ):
        result, ambiguous = await _post_once(
            session, "POST", "/test", json={}, deadline=1050.0
        )
        assert result == {"ok": True}
        assert ambiguous is False


# ---------------------------------------------------------------------------
# Orchestration: each section gets one attempt, all sections attempted,
# exactly one fallback note
# ---------------------------------------------------------------------------


class TestOrchestration:
    """Deterministic child failures do not prevent later sections or fallback."""

    @pytest.mark.asyncio
    async def test_all_sections_attempted_one_fallback(self, monkeypatch):
        call_log = []

        class _Sess:
            def __init__(self, *a, **kw):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            _ordered = [  # noqa: RUF012 - lightweight fake session constant
                "/incomes",
                "/additional_names",
                "/adverse_parties",
                "/notes",
                "/matters",
            ]

            def request(self, m, u, **kw):
                self.calls.append((m, u))
                call_log.append((m, u))
                path = next((p for p in self._ordered if p in u), "")
                if path == "/incomes":
                    response = (
                        _FakeResponse(200, {"data": []})
                        if m == "GET"
                        else _FakeResponse(400, {})
                    )
                    return _FakeRequestContextManager(response)
                if path == "/additional_names":
                    response = (
                        _FakeResponse(200, {"data": []})
                        if m == "GET"
                        else _FakeResponse(400, {})
                    )
                    return _FakeRequestContextManager(response)
                if path == "/adverse_parties":
                    response = (
                        _FakeResponse(200, {"data": []})
                        if m == "GET"
                        else _FakeResponse(400, {})
                    )
                    return _FakeRequestContextManager(response)
                if path == "/notes":
                    response = (
                        _FakeResponse(200, {"data": []})
                        if m == "GET"
                        else _FakeResponse(201, {})
                    )
                    return _FakeRequestContextManager(response)
                if "/matters" in u and m == "POST":
                    return _FakeRequestContextManager(
                        _FakeResponse(201, {"data": {"matter_uuid": "m-1"}})
                    )
                if "/matters" in u and m == "GET":
                    return _FakeRequestContextManager(_FakeResponse(200, {"data": []}))
                return _FakeRequestContextManager(_FakeResponse(201, {}))

            def get(self, u, **kw):
                return self.request("GET", u, **kw)

            def post(self, u, **kw):
                return self.request("POST", u, **kw)

        monkeypatch.setattr(aiohttp, "ClientSession", _Sess)
        result = await save_intake_legalserver(
            {
                "call_id": "c1",
                "names": {
                    "names": [{"first": "A", "last": "B"}, {"first": "C", "last": "D"}]
                },
                "income": {
                    "listing": {"J": {"E": {"amount": 50000, "period": "Annually"}}}
                },
                "adverse_parties": {"adverse_parties": [{"first": "X", "last": "Y"}]},
                "case_type": {"case_description": "desc"},
                "assets": {"listing": [{"savings": 100}], "total_value": 100},
            }
        )
        assert result.overall == LegalServerOverall.DEGRADED
        fb_notes = 0
        for m, u in call_log:
            if "/notes" in u:
                fb_notes += 1
        assert fb_notes >= 1  # at least the fallback
        income_count = len([c for c in call_log if "/incomes" in c[1]])
        assert income_count == 2  # collection read and one deterministic POST
        alias_count = len([c for c in call_log if "/additional_names" in c[1]])
        assert alias_count == 2
        ap_count = len([c for c in call_log if "/adverse_parties" in c[1]])
        assert ap_count == 2


# ---------------------------------------------------------------------------
# No call_id returns FAILED, not SKIPPED
# ---------------------------------------------------------------------------


class TestNoCallId:
    @pytest.mark.asyncio
    async def test_no_call_id_fails(self, monkeypatch):
        monkeypatch.setenv("LEGALSERVER_TESTING_DISABLE_CONNECTION", "false")
        result = await save_intake_legalserver(
            {
                "names": {"names": [{"first": "A", "last": "B"}]},
            }
        )
        assert result.overall == LegalServerOverall.FAILED


# ---------------------------------------------------------------------------
# Exact consolidated fallback payload assertions
# ---------------------------------------------------------------------------


def test_collect_fallback_payload_content():
    """Verify fallback content includes unresolved values and excludes successful."""
    ops = [
        RecordResult(
            OperationKind.INCOME,
            OperationOutcome.FAILED,
            _fallback_content="Employment: 50000 annually",
        ),
        RecordResult(
            OperationKind.INCOME, OperationOutcome.SUCCESS, _fallback_content=""
        ),
        RecordResult(OperationKind.ALIAS, OperationOutcome.SUCCESS),
        RecordResult(
            OperationKind.ALIAS,
            OperationOutcome.AMBIGUOUS,
            _fallback_content='{"first": "C", "last": "D"}',
        ),
        RecordResult(
            OperationKind.ADVERSE_PARTY,
            OperationOutcome.FAILED,
            _fallback_content='{"first": "X", "last": "Y"}',
        ),
        RecordResult(
            OperationKind.CASE_DESCRIPTION,
            OperationOutcome.AMBIGUOUS,
            _fallback_content="Landlord dispute",
        ),
        RecordResult(
            OperationKind.ASSETS,
            OperationOutcome.FAILED,
            _fallback_content="Assets: ... (total: 1000)",
        ),
        RecordResult(
            OperationKind.REJECTION_REASON,
            OperationOutcome.FAILED,
            _fallback_content="Over Income",
        ),
    ]
    parts = _collect_fallback_content(ops, None)
    combined = "\n".join(parts)
    assert "Employment: 50000 annually" in combined
    assert "Landlord dispute" in combined
    assert '"first": "C", "last": "D"' in combined
    assert '"first": "X", "last": "Y"' in combined
    assert "Assets:" in combined
    assert "Over Income" in combined
    assert "Employment: 50000 annually" in parts[0]  # income first
    # Successful siblings excluded
    assert "income" in parts[0].lower()
    assert "additional names" in parts[1].lower()


def test_collect_fallback_excludes_successful():
    ops = [
        RecordResult(
            OperationKind.INCOME,
            OperationOutcome.SUCCESS,
            _fallback_content="SHOULD NOT APPEAR",
        ),
        RecordResult(
            OperationKind.INCOME,
            OperationOutcome.FAILED,
            _fallback_content="Employment: 30000",
        ),
    ]
    parts = _collect_fallback_content(ops, None)
    combined = "\n".join(parts)
    assert "SHOULD NOT APPEAR" not in combined
    assert "Employment: 30000" in combined


# ---------------------------------------------------------------------------
# Deadline exhaustion orchestrations
# ---------------------------------------------------------------------------


class TestDeadlineOrchestration:
    """Deadline before section materializes unresolved operations."""

    @pytest.mark.asyncio
    async def test_deadline_before_fallback_produces_failed(self, monkeypatch):
        """Deadline exhausted before fallback attempt -> overall FAILED."""
        clock = _FakeClock(start=1000.0)

        class _Sess:
            def __init__(self, *a, **kw):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            _ordered = ["/incomes", "/notes", "/matters"]  # noqa: RUF012

            def request(self, m, u, **kw):
                self.calls.append((m, u))
                path = next((p for p in self._ordered if p in u), "")
                if path == "/incomes":
                    return _FakeRequestContextManager(_FakeResponse(400, {}))
                if path == "/notes":
                    return _FakeRequestContextManager(_FakeResponse(201, {}))
                if "/matters" in u and m == "POST":
                    return _FakeRequestContextManager(
                        _FakeResponse(201, {"data": {"matter_uuid": "m-1"}})
                    )
                if "/matters" in u and m == "GET":
                    return _FakeRequestContextManager(_FakeResponse(200, {"data": []}))
                return _FakeRequestContextManager(_FakeResponse(201, {}))

            def get(self, u, **kw):
                return self.request("GET", u, **kw)

            def post(self, u, **kw):
                return self.request("POST", u, **kw)

        monkeypatch.setattr(aiohttp, "ClientSession", _Sess)
        with (
            patch("intake_bot.services.legalserver._now", clock.now),
            patch("intake_bot.services.legalserver._sleep", clock.sleep),
            patch("intake_bot.services.legalserver.LEGALSERVER_TIMEOUT", 0.0),
        ):
            result = await save_intake_legalserver(
                {
                    "call_id": "c1",
                    "names": {"names": [{"first": "A", "last": "B"}]},
                    "income": {
                        "listing": {"J": {"E": {"amount": 50000, "period": "Annually"}}}
                    },
                }
            )
            assert result.overall == LegalServerOverall.FAILED


def test_record_result_repr_excludes_fallback():
    """repr() must not contain the private _fallback_content value."""
    r = RecordResult(
        kind=OperationKind.INCOME,
        outcome=OperationOutcome.FAILED,
        _fallback_content="SENTINEL-SHOULD-NOT-APPEAR",
    )
    rep = repr(r)
    assert "SENTINEL-SHOULD-NOT-APPEAR" not in rep
    assert "_fallback_content" not in rep
