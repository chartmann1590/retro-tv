"""Tests for Android companion app backend: pairing, device auth, and discovery."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import database
import scheduler
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

        # 6. List paired devices (token should be omitted, device_id included)
        devices = database.list_paired_devices()
        self.assertEqual(len(devices), 1)
        self.assertIn("device_id", devices[0])
        self.assertNotIn("token", devices[0])
        self.assertEqual(devices[0]["device_name"], "UMIDIGI A7 Pro")

        # 7. Revoke device by device_id
        dev_id = devices[0]["device_id"]
        revoked = database.revoke_paired_device(dev_id)
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

    def test_api_pairing_and_authorization_endpoints(self):
        # 1. Unauthenticated external request to /api/pair/devices should be rejected with 401
        unauth_res = self.client.get("/api/pair/devices", environ_base={"REMOTE_ADDR": "192.168.1.150"})
        self.assertEqual(unauth_res.status_code, 401)

        # 2. Generate code via Web UI endpoint (same-origin simulated)
        gen_res = self.client.post("/api/pair/generate", headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(gen_res.status_code, 200)
        gen_data = gen_res.get_json()
        code = gen_data["code"]
        self.assertEqual(len(code), 6)

        # 3. Verify code from companion app endpoint
        verify_res = self.client.post("/api/pair/verify", json={
            "code": code,
            "device_name": "UMIDIGI A7 Pro"
        })
        self.assertEqual(verify_res.status_code, 200)
        verify_data = verify_res.get_json()
        self.assertTrue(verify_data["ok"])
        token = verify_data["token"]

        # 4. Check status with X-Device-Token
        status_res = self.client.get("/api/pair/status", headers={"X-Device-Token": token})
        self.assertEqual(status_res.status_code, 200)
        self.assertTrue(status_res.get_json()["paired"])

        # 5. List devices with X-Device-Token authorization
        dev_res = self.client.get("/api/pair/devices", headers={"X-Device-Token": token},
                                  environ_base={"REMOTE_ADDR": "192.168.1.150"})
        self.assertEqual(dev_res.status_code, 200)
        devices = dev_res.get_json().get("devices", [])
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_name"], "UMIDIGI A7 Pro")
        self.assertNotIn("token", devices[0], "Raw token must not be exposed in devices list")
        device_id = devices[0]["device_id"]

        # 6. Unauthenticated revoke rejected
        unauth_revoke = self.client.post("/api/pair/revoke", json={"device_id": device_id},
                                         environ_base={"REMOTE_ADDR": "192.168.1.150"})
        self.assertEqual(unauth_revoke.status_code, 401)

        # 7. Authorized revoke using device_id from web UI
        revoke_res = self.client.post("/api/pair/revoke", json={"device_id": device_id},
                                      headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(revoke_res.status_code, 200)
        self.assertTrue(revoke_res.get_json()["ok"])

        # Check status after revoke
        status_res2 = self.client.get("/api/pair/status", headers={"X-Device-Token": token})
        self.assertEqual(status_res2.status_code, 200)
        self.assertFalse(status_res2.get_json()["paired"])

    def test_api_guide_timestamps_formatting(self):
        # Insert a dummy channel and schedule entry
        con = database.connect()
        try:
            con.execute("INSERT OR REPLACE INTO channels(number, name, enabled) VALUES(1, 'Test Channel', 1)")
            now = 1700000000.0
            con.execute("""INSERT INTO schedule_entries(channel_number, start_ts, end_ts, kind, media_id, title, day)
                           VALUES(1, ?, ?, 'episode', 101, 'Test Show S01E01', '2023-11-14')""", (now, now + 1800.0))
            con.commit()
        finally:
            con.close()

        res = self.client.get("/api/guide?start=1700000000&hours=2")
        self.assertEqual(res.status_code, 200)
        guide = res.get_json().get("guide", [])
        self.assertTrue(len(guide) > 0)
        entries = guide[0].get("entries", [])
        self.assertTrue(len(entries) > 0)
        entry = entries[0]
        self.assertIn("start_fmt", entry)
        self.assertIn("end_fmt", entry)
        self.assertTrue(len(entry["start_fmt"]) > 0)
        self.assertTrue(len(entry["end_fmt"]) > 0)


if __name__ == "__main__":
    unittest.main()
