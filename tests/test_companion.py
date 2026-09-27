"""Tests for Android companion app backend: pairing, device auth, and discovery."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import database
from app import app


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {"DB_PATH": os.path.join(self.tmp.name, "test.db"),
                           "DATA_DIR": self.tmp.name}.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.client = app.test_client()

    def test_database_pair_code_lifecycle(self):
        # 1. Create pair code
        database.create_pair_code("123456", expires_sec=600)

        # 2. Verify invalid code
        token_bad = database.verify_and_consume_pair_code("000000", device_name="Phone")
        self.assertIsNone(token_bad)

        # 3. Verify correct code
        token = database.verify_and_consume_pair_code("123456", device_name="UMIDIGI A7 Pro")
        self.assertIsNotNone(token)

        # 4. Verify code cannot be reused (consumed)
        token2 = database.verify_and_consume_pair_code("123456", device_name="UMIDIGI A7 Pro")
        self.assertIsNone(token2)

        # 5. Verify device is paired
        self.assertTrue(database.is_device_paired(token))

        # 6. List paired devices
        devices = database.list_paired_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["token"], token)
        self.assertEqual(devices[0]["device_name"], "UMIDIGI A7 Pro")

        # 7. Revoke device
        revoked = database.revoke_paired_device(token)
        self.assertTrue(revoked)
        self.assertFalse(database.is_device_paired(token))

    def test_api_server_identity(self):
        res = self.client.get("/api/server/identity")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["app"], "retro-tv")
        self.assertEqual(data["name"], "Retro TV")
        self.assertEqual(data["version"], "1.0.0")
        self.assertIn("features", data)

    def test_api_pairing_endpoints(self):
        # 1. Generate code via Web UI endpoint
        gen_res = self.client.post("/api/pair/generate")
        self.assertEqual(gen_res.status_code, 200)
        gen_data = gen_res.get_json()
        code = gen_data["code"]
        self.assertEqual(len(code), 6)

        # 2. Verify code from companion app endpoint
        verify_res = self.client.post("/api/pair/verify", json={
            "code": code,
            "device_name": "UMIDIGI A7 Pro"
        })
        self.assertEqual(verify_res.status_code, 200)
        verify_data = verify_res.get_json()
        self.assertTrue(verify_data["ok"])
        token = verify_data["token"]

        # 3. Check status
        status_res = self.client.get("/api/pair/status", headers={"X-Device-Token": token})
        self.assertEqual(status_res.status_code, 200)
        self.assertTrue(status_res.get_json()["paired"])

        # 4. List devices
        dev_res = self.client.get("/api/pair/devices")
        self.assertEqual(dev_res.status_code, 200)
        devices = dev_res.get_json().get("devices", [])
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_name"], "UMIDIGI A7 Pro")

        # 5. Revoke
        revoke_res = self.client.post("/api/pair/revoke", json={"token": token})
        self.assertEqual(revoke_res.status_code, 200)
        self.assertTrue(revoke_res.get_json()["ok"])

        # Check status after revoke
        status_res2 = self.client.get("/api/pair/status", headers={"X-Device-Token": token})
        self.assertEqual(status_res2.status_code, 200)
        self.assertFalse(status_res2.get_json()["paired"])


if __name__ == "__main__":
    unittest.main()
