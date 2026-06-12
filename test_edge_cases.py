import pytest
from utils.dm_helpers import run_cmd, DMTarget, get_dm_xor_table
import uuid

def test_invalid_device_count(loop_devices):
    """Test that creating a dm-xor target with 0 or 1 device fails (if that is the intended behavior)."""
    devs = loop_devices(count=1, size_mb=10)
    
    # Generate table with only 1 device
    table = get_dm_xor_table(devs)
    name = f"dm_xor_test_{uuid.uuid4().hex[:8]}"
    target = DMTarget(name, table)
    
    try:
        target.setup()
        pytest.fail("Target creation should have failed with only 1 device.")
    except Exception as e:
        # Expected behavior
        assert "Command failed" in str(e)
    finally:
        target.teardown()

def test_dm_flakey_composition(loop_devices, tmp_path):
    """Test dm-xor behavior when an underlying device throws errors using dm-flakey."""
    # We create two loop devices
    devs = loop_devices(count=2, size_mb=20)
    
    # We will wrap the first loop device in dm-flakey
    flakey_name = f"dm_flakey_{uuid.uuid4().hex[:8]}"
    size_bytes = int(run_cmd(f"sudo blockdev --getsize64 {devs[0]}"))
    size_sectors = size_bytes // 512
    
    # Flakey table: <start> <length> flakey <dev> <offset> <up interval> <down interval>
    # 0 up, 1 down (meaning it drops everything immediately)
    flakey_table = f"0 {size_sectors} flakey {devs[0]} 0 0 1"
    flakey_target = DMTarget(flakey_name, flakey_table)
    flakey_dev_path = flakey_target.setup()
    
    # Now we create dm-xor on top of [flakey_dev_path, devs[1]]
    xor_table = get_dm_xor_table([flakey_dev_path, devs[1]])
    xor_name = f"dm_xor_test_{uuid.uuid4().hex[:8]}"
    xor_target = DMTarget(xor_name, xor_table)
    
    try:
        xor_dev_path = xor_target.setup()
        
        # Try to write to dm-xor. Because flakey drops I/O, this should fail with an I/O error
        test_file = tmp_path / "test_data.bin"
        run_cmd(f"dd if=/dev/urandom of={test_file} bs=1M count=1 status=none")
        
        try:
            run_cmd(f"sudo dd if={test_file} of={xor_dev_path} bs=1M oflag=direct status=none")
            pytest.fail("Write should have failed due to underlying dm-flakey dropping I/O")
        except Exception as e:
            # We expect a failure. dd usually returns non-zero.
            pass
            
    finally:
        xor_target.teardown()
        flakey_target.teardown()
