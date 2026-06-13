import pytest
import time
import concurrent.futures
from utils.dm_helpers import run_cmd

def test_destruction_under_load(dm_xor, request):
    """
    Verify that destruction of an idle dm-xor target is fast and does not
    hang waiting for writes to complete on a separate active dm-xor target.
    """
    # Create the active target (the one that will receive heavy I/O)
    active_dev, active_backing = dm_xor(dev_count=2, size_mb=100)
    
    # Create the victim target (the one that will be destroyed)
    victim_dev, victim_backing = dm_xor(dev_count=2, size_mb=100)
    victim_name = victim_dev.split('/')[-1]

    def write_heavy_load():
        # Use fio for continuous random writes over 5 seconds
        fio_cmd = (
            f"sudo fio --name=stress --ioengine=libaio --iodepth=64 "
            f"--rw=randwrite --bs=4k --direct=1 "
            f"--runtime=5 --time_based --filename={active_dev} "
            f"--group_reporting"
        )
        return run_cmd(fio_cmd)

    # Enable fault injection delay via sysfs (Bit 0 = delay write)
    try:
        run_cmd("echo 1 | sudo tee /sys/module/dm_xor/parameters/enabled_faults")
    except Exception as e:
        pytest.skip(f"Could not enable fault injection (is module loaded?): {e}")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            # Start heavy IO on active_dev
            future = executor.submit(write_heavy_load)
            
            # Give fio a moment to spin up and saturate the device/workqueues
            time.sleep(1)
            
            # Measure destruction time of the idle victim device
            start_time = time.time()
            # Since dm_xor fixture automatically tears down, we tear it down manually here 
            # to measure the exact dmsetup remove latency.
            run_cmd(f"sudo dmsetup remove {victim_name}")
            end_time = time.time()
            
            destruction_duration_ms = (end_time - start_time) * 1000.0

            # Wait for the heavy IO to complete to ensure no errors occurred
            future.result()

    finally:
        # Always disable fault injection delay afterwards
        run_cmd("echo 0 | sudo tee /sys/module/dm_xor/parameters/enabled_faults", check=False)

    # Log the result
    if not hasattr(request.config, "performance_results"):
        request.config.performance_results = {}
    
    request.config.performance_results["Destruction Under Load Latency"] = [
        f"{destruction_duration_ms:.2f} ms"
    ]
    
    # Verify destruction was fast (e.g. under 200ms). If it shares a workqueue, 
    # it would take several seconds waiting for the 5-second fio run to finish.
    assert destruction_duration_ms < 200, f"Destruction took too long: {destruction_duration_ms:.2f} ms"
