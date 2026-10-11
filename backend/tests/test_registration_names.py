import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException, Request


@pytest.mark.parametrize('first,last', [('', 'Kaya'), ('Ada', ''), ('  ', 'Kaya'), (None, 'Kaya'), ({'$ne': ''}, 'Kaya')])
def test_blank_or_nonstring_names_rejected_before_db(first, last):
    scope, request, db = setup(first, last)
    with pytest.raises(HTTPException) as error:
        asyncio.run(scope['register'](request))
    assert error.value.detail == 'Ad ve soyad zorunludur'
    db.users.find_one.assert_not_awaited()


def setup(first, last):
    root = Path(__file__).parents[1]
    db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value={'id': 'existing'})))
    scope = {'Request': Request, 'HTTPException': HTTPException, 'datetime': datetime, 'timezone': timezone, 'db': db}
    for path, names in [('routes/deps.py', ['safe_str', 'is_safe_email']), ('routes/auth.py', ['register'])]:
        nodes = [n for n in ast.parse((root / path).read_text()).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        assert len(nodes) == len(names)
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), scope)
    request = SimpleNamespace(json=AsyncMock(return_value={'first_name': first, 'last_name': last,
                              'email': 'name-check@example.invalid', 'password': 'test-only-password'}))
    return scope, request, db


def test_turkish_names_pass_name_validation():
    scope, request, db = setup('  İlayda  ', '  Çağrı  ')
    with pytest.raises(HTTPException) as error:
        asyncio.run(scope['register'](request))
    assert error.value.detail == 'Bu e-posta zaten kayıtlı'
    db.users.find_one.assert_awaited_once()
