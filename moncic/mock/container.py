from pathlib import Path
from typing import Any, Callable

from moncic.container import Container, Result
from moncic.runner import CompletedCallable, RunConfig


class MockContainer(Container):
    """
    Mock container used for tests
    """

    def get_root(self) -> Path:
        return Path(self.properties["RootDirectory"])

    def _start(self):
        self.image.images.session.mock_log(system=self.image.name, action="container start")
        self.started = True

    def _stop(self):
        self.image.images.session.mock_log(system=self.image.name, action="container stop")
        self.started = False

    def run(self, command: list[str], config: RunConfig | None = None) -> CompletedCallable:
        run_config = self.config.run_config(config)
        self.image.images.session.mock_log(system=self.image.name, action="run", config=run_config, cmd=command)
        return self.image.images.session.get_process_result(args=command)

    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        run_config = self.config.run_config(config)
        self.image.images.session.mock_log(
            system=self.image.name,
            action="run callable",
            config=run_config,
            func=func.__name__,
            desc=func.__doc__,
            args=args,
            kwargs=kwargs,
        )
        return CompletedCallable(args=func.__name__, returncode=0)
