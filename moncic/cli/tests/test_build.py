import json

from moncic.runner import UserConfig
from moncic.unittest import CLITestCase
from moncic.unittest.sources import Package, SourcesTestCase


class CliBuildTests(CLITestCase, SourcesTestCase):
    def test_ci(self) -> None:
        package = self.get_package("hello")
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "ci", "test", package.path.as_posix())
        self.assertNoStderr(res)
        with self.match_run_log(self.session.run_log) as m:
            container_log = m.assertPopFirst("test: run container")
            with self.match_run_log(container_log) as cm:
                cm.assertPopFirst(
                    "forward_user", **UserConfig.from_current()._asdict()
                )
                cm.assertPopScript("Set up the container filesystem")
                cm.assertPopScript("Update container packages before build")
                cm.assertPopScript(f"Build {package.path}")

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
        self.assertEqual(output["result"]["name"], "hello")
        self.assertTrue(output["result"]["success"])
        self.assertEqual(output["result"]["trace_log"], [])

        self.assertIsInstance(output["source_history"], list)
