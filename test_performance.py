import pytest
import os
from utils.dm_helpers import run_cmd

@pytest.fixture(scope="module", autouse=True)
def check_fio():
    """Ensure fio is installed for performance testing."""
    try:
        run_cmd("fio --version")
    except Exception:
        pytest.skip("fio is not installed. Skipping performance tests.")

def _print_fio_results(test_name, output):
    """Parse and print fio results clearly."""
    print(f"\n========================================")
    print(f" {test_name} Performance Results")
    print(f"========================================")
    for line in output.split('\n'):
        if "IOPS=" in line:
            print("  " + line.strip())
    print(f"========================================\n")

def test_performance_fio_random_rw(dm_xor):
    """Run a simple fio benchmark on the dm-xor target to ensure no deadlocks and get a baseline."""
    # Create a reasonably sized target for fio
    xor_dev, backing_devs = dm_xor(dev_count=2, size_mb=200)
    
    fio_cmd = (
        f"sudo fio --name=randrw --ioengine=libaio --iodepth=16 "
        f"--rw=randrw --bs=4k --direct=1 --size=100M "
        f"--numjobs=4 --runtime=10 --group_reporting --filename={xor_dev}"
    )
    
    # We just ensure it runs successfully without crashing the kernel
    try:
        output = run_cmd(fio_cmd)
        
        # Basic sanity checks on fio output
        assert "error" not in output.lower(), "fio run completed but reported errors."
        assert "IOPS" in output, "fio run completed but IOPS not found in output."
        
        _print_fio_results("Random Read/Write", output)
                
    except Exception as e:
        pytest.fail(f"fio stress test failed: {e}")

def test_performance_fio_seq_write(dm_xor):
    """Run sequential write benchmark."""
    xor_dev, backing_devs = dm_xor(dev_count=3, size_mb=200)
    
    fio_cmd = (
        f"sudo fio --name=seqwrite --ioengine=libaio --iodepth=32 "
        f"--rw=write --bs=1M --direct=1 --size=150M "
        f"--numjobs=1 --group_reporting --filename={xor_dev}"
    )
    
    try:
        output = run_cmd(fio_cmd)
        assert "error" not in output.lower()
        assert "IOPS" in output
        
        _print_fio_results("Sequential Write", output)
    except Exception as e:
        pytest.fail(f"fio sequential write test failed: {e}")

