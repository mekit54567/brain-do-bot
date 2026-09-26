"""
Python 3.14 вычисляет аннотации лениво, а в Docker стоит 3.11, где они
вычисляются сразу при импорте. Этот тест принудительно вычисляет все
аннотации, чтобы ошибки вроде метода `list`, заслоняющего `list[dict]`,
находились на любой версии Python, а не только на сервере.
"""

import importlib
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(p.stem for p in ROOT.glob("*.py"))


def _annotated(module):
    yield module.__name__, module
    for name, value in vars(module).items():
        if getattr(value, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(value):
            yield f"{module.__name__}.{name}", value
        elif inspect.isclass(value):
            yield f"{module.__name__}.{name}", value
            for attr, member in vars(value).items():
                target = member.fget if isinstance(member, property) else getattr(member, "__func__", member)
                if inspect.isfunction(target):
                    yield f"{module.__name__}.{name}.{attr}", target


@pytest.mark.parametrize("module_name", MODULES)
def test_annotations_evaluate(module_name):
    module = importlib.import_module(module_name)
    for name, obj in _annotated(module):
        try:
            getattr(obj, "__annotations__", None)
        except Exception as e:  # pragma: no cover — сообщение важнее покрытия
            pytest.fail(f"{name}: {type(e).__name__}: {e}")
