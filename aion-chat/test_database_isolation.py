import unittest
from pathlib import Path

import config


class DatabaseIsolationTests(unittest.TestCase):
    def test_test_process_never_points_at_live_database(self):
        live_data = (config.BASE_DIR / "data").resolve(); live_database = (live_data / "chat.db").resolve()

        self.assertNotEqual(live_database, config.DB_PATH.resolve())
        self.assertIn("aionshome-tests-", str(config.DB_PATH.parent).lower()); self.assertNotEqual(live_data, config.DATA_DIR.resolve()); self._assert_all_derived_paths_isolated(live_data)

    def _assert_all_derived_paths_isolated(self, live_data: Path):
        import antigravity_cli, home_assistant_events, intimacy_skill, location, lounge_receptions, qq_social_process
        from qq_social import media as qq_media, worker as qq_worker
        from routes import homecoming, qq_social as qq_routes
        isolated = config.DATA_DIR.resolve()
        for path in (
            config.DB_PATH, config.SETTINGS_PATH, config.WORLDBOOK_PATH, config.UPLOADS_DIR,
            config.TTS_CACHE_DIR, config.THEATER_TTS_CACHE_DIR, config.CODEX_UPLOADS_DIR,
            location.LOCATION_CONFIG_PATH, location.LOCATION_STATUS_PATH,
            home_assistant_events.HA_CONFIG_PATH, home_assistant_events.EVENT_CONFIG_PATH,
            intimacy_skill._POOL_PATH, intimacy_skill._STATE_PATH,
            qq_social_process.DIRECTORY, qq_routes.DIRECTORY, qq_worker.DIRECTORY, qq_media.DIRECTORY,
            homecoming.RETURN_QUARANTINE_ROOT, antigravity_cli.CHAT_WORKSPACE,
            lounge_receptions.LOUNGE_DATABASE_PATH,
        ):
            resolved = Path(path).resolve()
            self.assertTrue(resolved.is_relative_to(isolated), f"{resolved} is not inside {isolated}")
            self.assertFalse(resolved.is_relative_to(live_data), f"{resolved} points into live {live_data}")


if __name__ == "__main__":
    unittest.main()
