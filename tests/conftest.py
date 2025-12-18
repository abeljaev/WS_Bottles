"""
Pytest конфигурация и общие фикстуры.
"""
import pytest
from pathlib import Path


@pytest.fixture
def project_root() -> Path:
    """Корневая директория проекта."""
    return Path(__file__).parent.parent


@pytest.fixture
def test_data_dir(project_root: Path) -> Path:
    """Директория с тестовыми данными."""
    return project_root / "tests" / "data"
