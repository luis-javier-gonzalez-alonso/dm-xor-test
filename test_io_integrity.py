import os
import hashlib
from utils.dm_helpers import run_cmd

def test_basic_read_write(dm_xor, tmp_path):
    """Test that data written to the dm-xor target can be read back correctly."""
    xor_dev, backing_devs = dm_xor(dev_count=2, size_mb=50)
    
    # Generate random data
    test_file = tmp_path / "test_data.bin"
    run_cmd(f"dd if=/dev/urandom of={test_file} bs=1M count=10 status=none")
    
    # Calculate checksum of original data
    orig_md5 = run_cmd(f"md5sum {test_file} | awk '{{print $1}}'")
    
    # Write to dm-xor device
    run_cmd(f"sudo dd if={test_file} of={xor_dev} bs=1M status=none")
    
    # Read back from dm-xor device
    read_file = tmp_path / "read_data.bin"
    run_cmd(f"sudo dd if={xor_dev} of={read_file} bs=1M count=10 status=none")
    
    # Calculate checksum of read data
    read_md5 = run_cmd(f"md5sum {read_file} | awk '{{print $1}}'")
    
    assert orig_md5 == read_md5, "Data integrity verification failed: read data does not match written data."

def test_filesystem_creation(dm_xor, tmp_path):
    """Test that a filesystem can be created and mounted on the dm-xor target."""
    xor_dev, backing_devs = dm_xor(dev_count=3, size_mb=100)
    
    # Format the device with ext4
    run_cmd(f"sudo mkfs.ext4 -F {xor_dev}")
    
    # Create mount point
    mnt_dir = tmp_path / "mnt"
    mnt_dir.mkdir()
    
    try:
        # Mount the device
        run_cmd(f"sudo mount {xor_dev} {mnt_dir}")
        
        # Write a file
        test_file = mnt_dir / "hello.txt"
        run_cmd(f"sudo bash -c 'echo \"Hello DM-XOR!\" > {test_file}'")
        
        # Unmount and remount
        run_cmd(f"sudo umount {mnt_dir}")
        run_cmd(f"sudo mount {xor_dev} {mnt_dir}")
        
        # Read the file
        content = run_cmd(f"sudo cat {test_file}")
        assert content == "Hello DM-XOR!", "Filesystem data persistence failed."
    finally:
        run_cmd(f"sudo umount {mnt_dir}", check=False)

def test_device_recreation_maintains_raw_data(loop_devices, tmp_path):
    """Test that destroying and recreating the dm-xor device preserves raw data."""
    import uuid
    from utils.dm_helpers import DMTarget, get_dm_xor_table
    
    devs = loop_devices(count=2, size_mb=50)
    table = get_dm_xor_table(devs)
    
    name1 = f"dm_xor_test_{uuid.uuid4().hex[:8]}"
    target1 = DMTarget(name1, table)
    
    try:
        xor_dev1 = target1.setup()
        
        test_file = tmp_path / "test_data.bin"
        run_cmd(f"dd if=/dev/urandom of={test_file} bs=1M count=10 status=none")
        orig_md5 = run_cmd(f"md5sum {test_file} | awk '{{print $1}}'")
        
        run_cmd(f"sudo dd if={test_file} of={xor_dev1} bs=1M oflag=direct status=none")
        
        # Destroy device
        target1.teardown()
        
        # Recreate device with same backing files
        name2 = f"dm_xor_test_{uuid.uuid4().hex[:8]}"
        target2 = DMTarget(name2, table)
        xor_dev2 = target2.setup()
        
        try:
            read_file = tmp_path / "read_data.bin"
            run_cmd("sudo sync && sudo sysctl -w vm.drop_caches=3", check=False)
            run_cmd(f"sudo dd if={xor_dev2} of={read_file} bs=1M count=10 iflag=direct status=none")
            
            read_md5 = run_cmd(f"md5sum {read_file} | awk '{{print $1}}'")
            assert orig_md5 == read_md5, "Raw data was lost or corrupted after recreating dm-xor device."
        finally:
            target2.teardown()
            
    finally:
        target1.teardown()

def test_device_recreation_maintains_filesystem(loop_devices, tmp_path):
    """Test that destroying and recreating the dm-xor device preserves an existing filesystem."""
    import uuid
    from utils.dm_helpers import DMTarget, get_dm_xor_table
    
    devs = loop_devices(count=3, size_mb=100)
    table = get_dm_xor_table(devs)
    
    name1 = f"dm_xor_test_{uuid.uuid4().hex[:8]}"
    target1 = DMTarget(name1, table)
    
    mnt_dir = tmp_path / "mnt2"
    mnt_dir.mkdir()
    
    try:
        xor_dev1 = target1.setup()
        
        # Format and mount
        run_cmd(f"sudo mkfs.ext4 -F {xor_dev1}")
        run_cmd(f"sudo mount {xor_dev1} {mnt_dir}")
        
        test_file = mnt_dir / "persistence.txt"
        run_cmd(f"sudo bash -c 'echo \"Data survives recreation!\" > {test_file}'")
        
        # Unmount and destroy the target
        run_cmd(f"sudo umount {mnt_dir}")
        target1.teardown()
        
        # Recreate the target with exact same backing devices
        name2 = f"dm_xor_test_{uuid.uuid4().hex[:8]}"
        target2 = DMTarget(name2, table)
        xor_dev2 = target2.setup()
        
        try:
            # Mount the newly created device
            run_cmd(f"sudo mount {xor_dev2} {mnt_dir}")
            
            content = run_cmd(f"sudo cat {test_file}")
            assert content == "Data survives recreation!", "Filesystem data was lost after recreating dm-xor device."
        finally:
            run_cmd(f"sudo umount {mnt_dir}", check=False)
            target2.teardown()
            
    finally:
        run_cmd(f"sudo umount {mnt_dir}", check=False)
        target1.teardown()
