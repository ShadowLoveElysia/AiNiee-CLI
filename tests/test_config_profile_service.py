import os
import tempfile
import unittest
from unittest import mock

from ModuleFolders.Infrastructure.TaskConfig import ConfigProfileService as service


class ActivePlatformThinkingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = self.temp_dir.name
        resource = os.path.join(root, "Resource")
        profiles = os.path.join(resource, "profiles")
        rules_profiles = os.path.join(resource, "rules_profiles")
        platforms = os.path.join(resource, "platforms")
        os.makedirs(profiles)
        os.makedirs(rules_profiles)
        os.makedirs(platforms)

        self.profile_path = os.path.join(profiles, "active.json")
        self.patchers = [
            mock.patch.object(service, "ROOT_CONFIG_FILE", os.path.join(resource, "config.json")),
            mock.patch.object(service, "PROFILES_PATH", profiles),
            mock.patch.object(service, "RULES_PROFILES_PATH", rules_profiles),
            mock.patch.object(service, "PRESET_PATH", os.path.join(platforms, "preset.json")),
        ]
        for patcher in self.patchers:
            patcher.start()

        service.atomic_write_json(
            service.ROOT_CONFIG_FILE,
            {"active_profile": "active", "active_rules_profile": "None"},
        )
        service.atomic_write_json(
            service.PRESET_PATH,
            {
                "platforms": {
                    "alpha": {"think_switch": True, "think_depth": "max"},
                    "beta": {"think_switch": True, "think_depth": "medium"},
                },
                "target_platform": "alpha",
                "api_settings": {"translate": "alpha", "polish": "beta"},
            },
        )
        service.atomic_write_json(
            self.profile_path,
            {
                "target_platform": "alpha",
                "api_settings": {"translate": "alpha", "polish": "beta"},
                "think_switch": True,
                "unrelated": {"keep": "unchanged"},
                "platforms": {
                    "alpha": {
                        "think_switch": True,
                        "think_depth": "max",
                        "api_key": "must-remain-unchanged",
                    },
                    "beta": {"think_switch": True, "think_depth": "medium"},
                },
            },
        )

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def load_profile(self):
        return service.load_json_file(self.profile_path, {})

    def test_updates_active_platform_and_top_level_without_exposing_secrets(self):
        result = service.set_active_platform_thinking(" alpha ", False)

        self.assertEqual(
            result,
            {
                "active_profile": "active",
                "platform_tag": "alpha",
                "think_switch": False,
                "think_depth": "max",
            },
        )
        profile = self.load_profile()
        self.assertFalse(profile["think_switch"])
        self.assertFalse(profile["platforms"]["alpha"]["think_switch"])
        self.assertEqual(profile["platforms"]["alpha"]["api_key"], "must-remain-unchanged")
        self.assertEqual(profile["unrelated"], {"keep": "unchanged"})
        self.assertNotIn("api_key", result)

    def test_non_active_platform_does_not_change_top_level_switch(self):
        result = service.set_active_platform_thinking("beta", False)

        profile = self.load_profile()
        self.assertTrue(profile["think_switch"])
        self.assertFalse(profile["platforms"]["beta"]["think_switch"])
        self.assertEqual(result["think_depth"], "medium")

    def test_unknown_platform_is_rejected_without_writing(self):
        with open(self.profile_path, "rb") as reader:
            before = reader.read()

        with self.assertRaises(KeyError):
            service.set_active_platform_thinking("missing-platform", False)

        with open(self.profile_path, "rb") as reader:
            self.assertEqual(reader.read(), before)

    def test_non_boolean_switch_is_rejected(self):
        with self.assertRaises(ValueError):
            service.set_active_platform_thinking("alpha", 0)


if __name__ == "__main__":
    unittest.main()
