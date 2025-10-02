import json

from moncic.exceptions import Fail
from moncic.unittest import CLITestCase
from moncic.runner import UserConfig


class CliBuildTests(CLITestCase):
    def test_ci(self) -> None:
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "ci", "test")
        self.assertNoStderr(res)
        with self.match_run_log(self.session.run_log) as m:
            container_log = m.assertPopFirst("test: container start")
            with self.match_run_log(container_log) as cm:
                cm.assertPopFirst("forward_user", **UserConfig.from_current()._asdict())
                cm.assertPopScript("Set up the container filesystem")
                cm.assertPopScript("Update container packages before build")
                cm.assertPopScript("Build .")
            m.assertPopFirst("test: container stop")
            m.assertEmpty()

        output = json.loads(res.stdout)
        self.assertEqual(
            output["config"],
            {
                "artifacts_dir": None,
                "on_end": [],
                "on_fail": [],
                "on_success": [],
                "quick": False,
                "source_only": False,
            },
        )

        self.assertEqual(output["result"]["artifacts"], [])
        self.assertEqual(output["result"]["name"], "moncic-ci")
        self.assertTrue(output["result"]["success"])
        self.assertEqual(output["result"]["trace_log"], [])

        self.assertIsInstance(output["source_history"], list)
