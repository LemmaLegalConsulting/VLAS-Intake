import pytest
from intake_bot.services.reference_data import ReferenceDataLoader


@pytest.fixture(scope="module")
def loader():
    return ReferenceDataLoader()


def test_classifier_taxonomy_is_sorted_list_of_labels(loader):
    taxonomy = loader.classifier_taxonomy
    assert isinstance(taxonomy, list)
    assert len(taxonomy) > 0
    assert taxonomy == sorted(taxonomy)
    assert "Private Landlord/Tenant" in taxonomy


def test_label_for_legal_problem_code_by_full_entry(loader):
    label = loader.label_for_legal_problem_code("63 Private Landlord/Tenant")
    assert label == "Private Landlord/Tenant"


def test_label_for_legal_problem_code_by_numeric_prefix(loader):
    label = loader.label_for_legal_problem_code("63")
    assert label == "Private Landlord/Tenant"


def test_label_for_legal_problem_code_unknown(loader):
    label = loader.label_for_legal_problem_code("999")
    assert label is None


def test_label_for_legal_problem_code_empty(loader):
    label = loader.label_for_legal_problem_code("")
    assert label is None


def test_legal_problem_code_from_label(loader):
    code = loader.legal_problem_code_from_label("Private Landlord/Tenant")
    assert code == "63 Private Landlord/Tenant"


def test_legal_problem_code_from_label_unknown(loader):
    code = loader.legal_problem_code_from_label("Nonexistent Problem")
    assert code is None
