import pytest
import time
import subprocess
import uuid
import concurrent.futures
from utils.dm_helpers import run_cmd, DMTarget

# Helper mask bits as defined in dm-xor.c
XOR_FAULT_DELAY_WRITE = 1 << 0
XOR_FAULT_DELAY_READ  = 1 << 1
XOR_FAULT_OOM_TRACKER = 1 << 2
XOR_FAULT_OOM_PAGE    = 1 << 3
XOR_FAULT_OOM_CLONE   = 1 << 4
XOR_FAULT_CRYPTO_FAIL = 1 << 5


@pytest.fixture
def inject_fault():
    """Fixture to safely inject and clear faults."""
    def _inject(fault_mask):
        run_cmd(f"sudo sh -c 'echo {fault_mask} > /sys/module/dm_xor/parameters/enabled_faults'")
    
    yield _inject
    
    # Teardown: ensure faults are cleared after test
    run_cmd("sudo sh -c 'echo 0 > /sys/module/dm_xor/parameters/enabled_faults'", check=False)

def _store_fio_results(request, test_name, output):
    """Parse fio results clearly and store them in pytest config."""
    if not hasattr(request.config, "performance_results"):
        request.config.performance_results = {}
        
    lines = []
    for line in output.split('\n'):
        if "IOPS=" in line:
            lines.append(line.strip())
            
    request.config.performance_results[test_name] = lines


# ==============================================================================
# SECTION 1: Internal Module Faults
# ==============================================================================

def test_internal_fault_oom_tracker(dm_xor, inject_fault):
    """
    Test: XOR_FAULT_OOM_TRACKER
    Goal: Verify behavior when the initial mempool_alloc for xor_io_tracker fails.
    Expected: The module should return DM_MAPIO_REQUEUE, causing the block layer
              to retry gracefully without crashing the kernel.
    """
    dev_path, _ = dm_xor()
    
    # Inject OOM tracker fault
    inject_fault(XOR_FAULT_OOM_TRACKER)
    
    # Issue a write in the background. Since allocation fails, the block layer
    # will requeue the bio indefinitely without crashing.
    def run_io():
        # This will block until the fault is cleared and the requeued bio succeeds
        return run_cmd(f"sudo dd if=/dev/urandom of={dev_path} bs=4k count=1 oflag=direct", check=False)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(run_io)
        time.sleep(0.5)  # Wait for it to get stuck
        
        # Clear the fault, IO should now complete
        inject_fault(0)
        future.result(timeout=5.0)


def test_internal_fault_oom_page_fallback(dm_xor, inject_fault, request):
    """
    Test: XOR_FAULT_OOM_PAGE
    Goal: Verify the slow-path fallback logic for bounce page allocations.
    Expected: By artificially failing the fast path, the module must lock the
              page_lock and fall back to serialized allocation. I/O should complete
              successfully, albeit slower, and no deadlocks should occur.
    """
    dev_path, _ = dm_xor(dev_count=2, size_mb=100)
    
    # Baseline Run (No faults)
    fio_cmd = (f"sudo fio --name=test --filename={dev_path} --rw=randrw --rwmixread=50 "
               f"--bs=4k --size=50M --direct=1 --verify=pattern --verify_pattern=0xdeadbeef --do_verify=1")
    
    baseline_out = run_cmd(fio_cmd)
    _store_fio_results(request, "OOM_PAGE_Baseline", baseline_out)
    
    # Fault Run
    inject_fault(XOR_FAULT_OOM_PAGE)
    fault_out = run_cmd(fio_cmd)
    _store_fio_results(request, "OOM_PAGE_Degraded", fault_out)


def test_internal_fault_oom_clone_fallback(dm_xor, inject_fault, request):
    """
    Test: XOR_FAULT_OOM_CLONE
    Goal: Verify the slow-path fallback logic for clone bio allocations.
    Expected: Similar to page OOM, forcing clone_fallback should serialize
              allocations via clone_lock and complete without dropping data.
    """
    dev_path, _ = dm_xor(dev_count=2, size_mb=100)
    
    # Baseline Run (No faults)
    fio_cmd = (f"sudo fio --name=test --filename={dev_path} --rw=randrw --rwmixread=50 "
               f"--bs=4k --size=50M --direct=1 --verify=pattern --verify_pattern=0xcafebabe --do_verify=1")
    
    baseline_out = run_cmd(fio_cmd)
    _store_fio_results(request, "OOM_CLONE_Baseline", baseline_out)
    
    # Fault Run
    inject_fault(XOR_FAULT_OOM_CLONE)
    fault_out = run_cmd(fio_cmd)
    _store_fio_results(request, "OOM_CLONE_Degraded", fault_out)


def test_internal_fault_crypto_failure(dm_xor, inject_fault):
    """
    Test: XOR_FAULT_CRYPTO_FAIL
    Goal: Verify cleanup when ChaCha20 fails to generate noise.
    Expected: The write worker must clean up bounce pages and clones, then
              fail the original bio (BLK_STS_IOERR). It must NOT write uninitialized
              kernel memory to the physical disks.
    """
    dev_path, _ = dm_xor()
    inject_fault(XOR_FAULT_CRYPTO_FAIL)
    
    # Write should fail cleanly with I/O error
    res = subprocess.run(f"sudo dd if=/dev/zero of={dev_path} bs=4k count=1 oflag=direct", 
                         shell=True, stderr=subprocess.PIPE, text=True)
    assert res.returncode != 0
    assert "error" in res.stderr.lower()


def test_internal_fault_delayed_reads(dm_xor, inject_fault):
    """
    Test: XOR_FAULT_DELAY_READ
    Goal: Ensure adding delay to the softirq decode_inline() path does not lock
          up the system and reads eventually complete.
    Expected: Reads complete but take noticeably longer (>100ms per bio).
    """
    dev_path, _ = dm_xor()
    
    # Setup some data first
    run_cmd(f"sudo dd if=/dev/zero of={dev_path} bs=4k count=5 oflag=direct")
    
    inject_fault(XOR_FAULT_DELAY_READ)
    
    start_time = time.time()
    run_cmd(f"sudo dd if={dev_path} of=/dev/null bs=4k count=5 iflag=direct")
    end_time = time.time()
    
    # 5 reads, each delayed by 100ms
    assert (end_time - start_time) >= 0.5


def test_internal_fault_destruction_under_load(dm_xor, inject_fault, request):
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
        # Use fio for continuous random writes over a short 2-second window
        fio_cmd = (
            f"sudo fio --name=stress --ioengine=libaio --iodepth=8 "
            f"--rw=randwrite --bs=4k --direct=1 "
            f"--runtime=2 --time_based --filename={active_dev} "
            f"--group_reporting"
        )
        return run_cmd(fio_cmd)

    # Enable fault injection delay via sysfs (Bit 0 = delay write)
    inject_fault(XOR_FAULT_DELAY_WRITE)

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

        # Wait for the heavy IO to complete to ensure no errors occurred.
        try:
            future.result(timeout=10)
        except concurrent.futures.TimeoutError:
            pytest.fail("Active target I/O locked up completely! The global workqueue dropped a bio during drain.")

    _store_fio_results(request, "Destruction Under Load Latency", f"IOPS={destruction_duration_ms:.2f} ms")
    
    assert destruction_duration_ms < 100, f"Destruction took too long: {destruction_duration_ms:.2f} ms"


# ==============================================================================
# SECTION 2: External Hardware/DM Targets Simulating Errors
# ==============================================================================

def test_external_missing_drive_dm_error(loop_devices):
    """
    Test: dm-error stack
    Goal: Simulate a catastrophic failure of one underlying drive during I/O.
    Expected: `dm-xor` requires all chunks. If one drive (dm-error) instantly
              returns I/O errors, dm-xor should correctly abort the bio with an
              I/O error back to the application, without hanging.
    """
    devs = loop_devices(count=2, size_mb=10)
    size_bytes = int(run_cmd(f"sudo blockdev --getsize64 {devs[1]}"))
    size_sectors = size_bytes // 512
    
    # Setup dm-error
    error_table = f"0 {size_sectors} error"
    error_name = f"dm_error_{uuid.uuid4().hex[:8]}"
    error_target = DMTarget(error_name, error_table)
    error_dev = error_target.setup()
    
    # Setup dm-xor using devs[0] and the dm-error device
    xor_table = f"0 {size_sectors} xor {devs[0]} {error_dev}"
    xor_name = f"dm_xor_{uuid.uuid4().hex[:8]}"
    xor_target = DMTarget(xor_name, xor_table)
    xor_dev = xor_target.setup()
    
    try:
        # I/O to dm-xor should fail immediately with I/O error
        res = subprocess.run(f"sudo dd if=/dev/zero of={xor_dev} bs=4k count=1 oflag=direct", 
                             shell=True, stderr=subprocess.PIPE, text=True)
        assert res.returncode != 0
        assert "error" in res.stderr.lower()
    finally:
        xor_target.teardown()
        error_target.teardown()


def test_external_flaky_drive_dm_flakey(loop_devices):
    """
    Test: dm-flakey stack
    Goal: Simulate a drive that sporadically drops reads or writes.
    Expected: dm-xor should reflect the dropped I/O as errors, while successful
              I/Os pass through correctly.
    """
    devs = loop_devices(count=2, size_mb=10)
    size_bytes = int(run_cmd(f"sudo blockdev --getsize64 {devs[1]}"))
    size_sectors = size_bytes // 512
    
    # Setup dm-flakey: up for 1s, down for 1s. During down, returns EIO.
    flakey_table = f"0 {size_sectors} flakey {devs[1]} 0 1 1"
    flakey_name = f"dm_flakey_{uuid.uuid4().hex[:8]}"
    flakey_target = DMTarget(flakey_name, flakey_table)
    flakey_dev = flakey_target.setup()
    
    xor_table = f"0 {size_sectors} xor {devs[0]} {flakey_dev}"
    xor_name = f"dm_xor_{uuid.uuid4().hex[:8]}"
    xor_target = DMTarget(xor_name, xor_table)
    xor_dev = xor_target.setup()
    
    try:
        # Initial I/O should succeed (up phase)
        run_cmd(f"sudo dd if=/dev/zero of={xor_dev} bs=4k count=1 oflag=direct")
        
        # Sleep to enter down phase
        time.sleep(1.5)
        
        # I/O should fail (down phase)
        res = subprocess.run(f"sudo dd if=/dev/zero of={xor_dev} bs=4k count=1 oflag=direct", 
                             shell=True, stderr=subprocess.PIPE, text=True)
        assert res.returncode != 0
        assert "error" in res.stderr.lower()
    finally:
        xor_target.teardown()
        flakey_target.teardown()


def test_external_slow_drive_dm_delay(loop_devices):
    """
    Test: dm-delay stack
    Goal: Simulate one backing disk experiencing high latency.
    Expected: Concurrent I/O should continue, but completions are delayed. The
              workqueue and atomic pending counters should correctly wait for
              the slowest clone bio to complete before finishing the original bio.
    """
    devs = loop_devices(count=2, size_mb=10)
    size_bytes = int(run_cmd(f"sudo blockdev --getsize64 {devs[1]}"))
    size_sectors = size_bytes // 512
    
    # Setup dm-delay: 500ms delay for all reads and writes
    delay_table = f"0 {size_sectors} delay {devs[1]} 0 500"
    delay_name = f"dm_delay_{uuid.uuid4().hex[:8]}"
    delay_target = DMTarget(delay_name, delay_table)
    delay_dev = delay_target.setup()
    
    xor_table = f"0 {size_sectors} xor {devs[0]} {delay_dev}"
    xor_name = f"dm_xor_{uuid.uuid4().hex[:8]}"
    xor_target = DMTarget(xor_name, xor_table)
    xor_dev = xor_target.setup()
    
    try:
        start_time = time.time()
        run_cmd(f"sudo dd if=/dev/zero of={xor_dev} bs=4k count=1 oflag=direct")
        end_time = time.time()
        
        # Expect at least 500ms delay due to dm-delay target
        assert (end_time - start_time) >= 0.5
    finally:
        xor_target.teardown()
        delay_target.teardown()


def test_external_read_bad_sector_dm_dust(loop_devices):
    """
    Test: dm-dust stack
    Goal: Simulate a single bad sector (read error) on one of the underlying drives.
    Expected: dm-xor requires all chunks to reconstruct data. If a sector cannot
              be read from one drive, the entire read must fail with an I/O error
              instead of returning corrupted or zeroed data.
    """
    devs = loop_devices(count=2, size_mb=10)
    size_bytes = int(run_cmd(f"sudo blockdev --getsize64 {devs[1]}"))
    size_sectors = size_bytes // 512
    
    # Setup dm-dust: <start> <length> dust <dev path> <offset> <blksz>
    dust_table = f"0 {size_sectors} dust {devs[1]} 0 512"
    dust_name = f"dm_dust_{uuid.uuid4().hex[:8]}"
    dust_target = DMTarget(dust_name, dust_table)
    dust_dev = dust_target.setup()
    
    # Setup dm-xor using devs[0] and the dm-dust device
    xor_table = f"0 {size_sectors} xor {devs[0]} {dust_dev}"
    xor_name = f"dm_xor_{uuid.uuid4().hex[:8]}"
    xor_target = DMTarget(xor_name, xor_table)
    xor_dev = xor_target.setup()
    
    try:
        # Write some initial data to the target
        run_cmd(f"sudo dd if=/dev/urandom of={xor_dev} bs=4k count=1 oflag=direct")
        
        # Add a bad block at block 0 (which is sector 0) and enable dust
        run_cmd(f"sudo dmsetup message {dust_name} 0 addbadblock 0")
        run_cmd(f"sudo dmsetup message {dust_name} 0 enable")
        
        # Reading from the dm-xor target should now fail, because it will hit the bad sector
        # on the dm-dust target.
        res = subprocess.run(f"sudo dd if={xor_dev} of=/dev/null bs=4k count=1 iflag=direct", 
                             shell=True, stderr=subprocess.PIPE, text=True)
        assert res.returncode != 0
        assert "error" in res.stderr.lower(), "Read should have failed due to bad sector"
        
    finally:
        xor_target.teardown()
        dust_target.teardown()


def test_external_write_bad_sector_dm_dust(loop_devices):
    """
    Test: dm-dust stack (Write Failure)
    Goal: Simulate a single bad sector (write error) on one of the underlying drives.
    Expected: dm-xor requires all chunks to reconstruct data. If a write fails on 
              one drive, the entire write must fail with an I/O error instead of 
              silently dropping data.
    """
    devs = loop_devices(count=2, size_mb=10)
    size_bytes = int(run_cmd(f"sudo blockdev --getsize64 {devs[1]}"))
    size_sectors = size_bytes // 512
    
    # Setup dm-dust: <start> <length> dust <dev path> <offset> <blksz>
    dust_table = f"0 {size_sectors} dust {devs[1]} 0 512"
    dust_name = f"dm_dust_{uuid.uuid4().hex[:8]}"
    dust_target = DMTarget(dust_name, dust_table)
    dust_dev = dust_target.setup()
    
    # Setup dm-xor using devs[0] and the dm-dust device
    xor_table = f"0 {size_sectors} xor {devs[0]} {dust_dev}"
    xor_name = f"dm_xor_{uuid.uuid4().hex[:8]}"
    xor_target = DMTarget(xor_name, xor_table)
    xor_dev = xor_target.setup()
    
    try:
        # Add a bad block at block 0 (which is sector 0)
        run_cmd(f"sudo dmsetup message {dust_name} 0 addbadblock 0")
        
        # If the kernel dm-dust supports failing writes (e.g. patched kernel), enable it.
        # Otherwise, this will just fail the message command, and we ignore it.
        run_cmd(f"sudo dmsetup message {dust_name} 0 fail_write_on_bad_block", check=False)
        run_cmd(f"sudo dmsetup message {dust_name} 0 enable")
        
        # Writing to the dm-xor target should now fail, because it will hit the bad sector
        res = subprocess.run(f"sudo dd if=/dev/urandom of={xor_dev} bs=4k count=1 oflag=direct", 
                             shell=True, stderr=subprocess.PIPE, text=True)
        
        assert res.returncode != 0
        assert "error" in res.stderr.lower(), "Write should have failed due to bad sector"
        
    finally:
        xor_target.teardown()
        dust_target.teardown()
