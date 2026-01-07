"""
Diagnostic script to find all Space Mouse HID data.
Run this to troubleshoot HID interface issues.

NOTE: Run diagnose_spacemouse.bat as Admin to kill the driver first!
"""
import sys
import struct
import time
import os

try:
    import hid
except ImportError:
    print("ERROR: hidapi not installed. Run: pip install hidapi")
    sys.exit(1)

VENDOR_3DCONNEXION = 0x256F

# Output to both console and file
output_file = os.path.join(os.path.dirname(__file__), 'diagnostic_results.txt')
results = []

def log(msg):
    print(msg)
    results.append(msg)

log("=" * 60)
log("Space Mouse HID Diagnostic")
log("=" * 60)

# List ALL 3Dconnexion interfaces
log("\nAll 3Dconnexion HID interfaces:")
all_interfaces = []
for d in hid.enumerate():
    if d['vendor_id'] == VENDOR_3DCONNEXION:
        all_interfaces.append(d)
        log(f"\n  Interface {len(all_interfaces)-1}:")
        log(f"    Product: {d['product_string']}")
        log(f"    usage_page: {d['usage_page']}, usage: {d['usage']}")
        log(f"    interface: {d['interface_number']}")

if not all_interfaces:
    log("\nNo 3Dconnexion devices found!")
    log("Make sure the 3Dconnexion driver is STOPPED.")
    with open(output_file, 'w') as f:
        f.write('\n'.join(results))
    sys.exit(1)

# Try to open each interface and read data
log("\n" + "=" * 60)
log("Testing each interface for data...")
log("Move and TWIST/TILT the Space Mouse for 5 seconds!")
log("=" * 60)

for idx, d in enumerate(all_interfaces):
    log(f"\n--- Interface {idx}: usage_page={d['usage_page']}, usage={d['usage']} ---")
    try:
        device = hid.device()
        device.open_path(d['path'])
        device.set_nonblocking(True)
        
        start = time.time()
        report_ids_seen = {}
        
        while time.time() - start < 5.0:
            data = device.read(64)
            if data:
                report_id = data[0]
                data_len = len(data)
                
                if report_id not in report_ids_seen:
                    report_ids_seen[report_id] = {'count': 0, 'max_len': 0, 'samples': []}
                    log(f"  [NEW] Report ID {report_id}: len={data_len}, raw={[hex(b) for b in data[:15]]}")
                
                report_ids_seen[report_id]['count'] += 1
                report_ids_seen[report_id]['max_len'] = max(report_ids_seen[report_id]['max_len'], data_len)
                
                if len(report_ids_seen[report_id]['samples']) < 5:
                    report_ids_seen[report_id]['samples'].append(list(data[:15]))
            
            time.sleep(0.005)
        
        device.close()
        
        if report_ids_seen:
            for rid, info in report_ids_seen.items():
                log(f"  Report ID {rid}: {info['count']} samples, max_len={info['max_len']}")
                
                if info['samples']:
                    sample = info['samples'][0]
                    if len(sample) >= 7:
                        v1 = struct.unpack('<h', bytes(sample[1:3]))[0]
                        v2 = struct.unpack('<h', bytes(sample[3:5]))[0]
                        v3 = struct.unpack('<h', bytes(sample[5:7]))[0]
                        log(f"    Parsed [1:7]: {v1}, {v2}, {v3}")
                    if len(sample) >= 13:
                        v4 = struct.unpack('<h', bytes(sample[7:9]))[0]
                        v5 = struct.unpack('<h', bytes(sample[9:11]))[0]
                        v6 = struct.unpack('<h', bytes(sample[11:13]))[0]
                        log(f"    Parsed [7:13]: {v4}, {v5}, {v6}")
                
                if rid == 1:
                    log(f"    ^ TRANSLATION (x, y, z) or combined with rotation")
                elif rid == 2:
                    log(f"    ^ ROTATION (rx, ry, rz)")
                elif rid == 3:
                    log(f"    ^ BUTTONS")
        else:
            log("  No data received (driver may still be running)")
            
    except Exception as e:
        log(f"  Could not open: {e}")

log("\n" + "=" * 60)
log("ANALYSIS:")
log("- Report ID 1 with 13+ bytes = Translation + Rotation combined")
log("- Report ID 1 + Report ID 2 = Separate translation/rotation")
log("=" * 60)

with open(output_file, 'w') as f:
    f.write('\n'.join(results))
log(f"\nResults saved to: {output_file}")
