import pytest
import uuid
from utils.dm_helpers import LoopDevice, DMTarget, get_dm_xor_table, run_cmd

@pytest.fixture(scope="function")
def loop_devices():
    """Provides a list of loop devices and cleans them up after the test."""
    devices = []
    
    def _create_devices(count=2, size_mb=100):
        for _ in range(count):
            dev = LoopDevice(size_mb=size_mb)
            dev.setup()
            devices.append(dev)
        return [d.loop_dev for d in devices]

    yield _create_devices

    # Teardown
    for dev in devices:
        dev.teardown()

@pytest.fixture(scope="function")
def dm_xor(loop_devices):
    """Provides a dm-xor target built on top of dynamically created loop devices."""
    targets = []

    def _create_dm_xor(dev_count=2, size_mb=100):
        devs = loop_devices(count=dev_count, size_mb=size_mb)
        table = get_dm_xor_table(devs)
        name = f"dm_xor_test_{uuid.uuid4().hex[:8]}"
        target = DMTarget(name, table)
        dev_path = target.setup()
        targets.append(target)
        return dev_path, devs

    yield _create_dm_xor

    # Teardown
    for target in targets:
        target.teardown()

@pytest.fixture(scope="session", autouse=True)
def check_root():
    """Ensure tests are run as root, since losetup and dmsetup require it."""
    import os
    if os.geteuid() != 0:
        pytest.exit("These tests must be run as root (e.g., via sudo) to use dmsetup and losetup.")

@pytest.fixture(scope="session", autouse=True)
def load_module():
    """Attempt to load the dm-xor module if not loaded."""
    run_cmd("sudo modprobe dm-xor", check=False)
