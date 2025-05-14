from .system import MaintenanceMixin, NspawnSystem


class MockSystem(NspawnSystem):
    def local_run(self, cmd: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.

        This is used for bootstrapping or removing a system.
        """
        self.images.session.mock_log(system=self.name, action="local_run", config=config, cmd=cmd)
        return self.images.session.get_process_result(args=cmd)

    def create_container(self, instance_name: str | None = None, config: ContainerConfig | None = None) -> Container:
        """
        Boot a container with this system
        """
        from moncic.container import MockContainer

        config = self.container_config(config)
        return MockContainer(self, config, instance_name)


class MockMaintenanceSystem(MaintenanceMixin, MockSystem):
    pass
