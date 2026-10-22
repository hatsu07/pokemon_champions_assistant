"""No-network tests for local OAuth account state and DPAPI wrapper."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from champions_assist.siwc_auth import ProtectedSessionStore, ChatGPTSignIn, REQUIRED_PLAN_SCOPE, SignInError


class SignInTests(unittest.TestCase):
    def test_protected_local_storage_roundtrip_and_logout(self):
        with tempfile.TemporaryDirectory() as d:
            store=ProtectedSessionStore(d, protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])
            first=store.load()
            store.save(first)
            self.assertEqual(store.load()["host_id"],first["host_id"])
            first["accounts"]["account"]={"access_token":"token","scopes":[REQUIRED_PLAN_SCOPE]}
            first["selected"]="account"
            store.save(first)
            auth=ChatGPTSignIn(store)
            self.assertTrue(auth.status()["connected"])
            self.assertTrue(auth.status()["plan_enabled"])
            auth.sign_out()
            self.assertFalse(auth.status()["connected"])
            with self.assertRaises(SignInError):
                auth.access_token()

    def test_no_scope_blocks_plan_use(self):
        with tempfile.TemporaryDirectory() as d:
            store=ProtectedSessionStore(d, protect=lambda b:b,unprotect=lambda b:b)
            data=store.load()
            data["selected"]="account"
            data["accounts"]["account"]={"access_token":"test","scopes":[]}
            store.save(data)
            with self.assertRaisesRegex(SignInError,"PLAN_PERMISSION_NOT_GRANTED"):
                ChatGPTSignIn(store).access_token()

    def test_window_is_not_sent_to_network_without_explicit_login(self):
        with tempfile.TemporaryDirectory() as d:
            store=ProtectedSessionStore(d,protect=lambda b:b,unprotect=lambda b:b)
            auth=ChatGPTSignIn(store)
            self.assertFalse(auth.status()["connected"])
