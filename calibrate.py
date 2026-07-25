#!/usr/bin/env python3
"""
Calibration tool for vibrate_player.py
Measures your MacBook's accelerometer baseline noise and vibration response,
then saves a calibration profile that vibrate_player.py uses for accurate triggering.

Usage:
    sudo python3 calibrate.py
    sudo python3 calibrate.py --output my_profile.json
"""

import os
import sys
import time
import subprocess
import argparse
import json
import math
import datetime
from collections import deque

try:
    from dotenv import load_dotenv
except ImportError:
    print("Please install python-dotenv: pip install python-dotenv")
    sys.exit(1)

# Load environment variables from .env file
load_dotenv()

# Auto-escalate to sudo if not root
if os.geteuid() != 0:
    mac_password = os.getenv("MAC_PASSWORD")
    if not mac_password:
        print("Please set MAC_PASSWORD in .env file or run this script with sudo.")
        sys.exit(1)

    print("Elevating privileges with password from .env...")
    cmd = ['sudo', '-S', sys.executable] + sys.argv
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, universal_newlines=True)
    try:
        p.communicate(mac_password + '\n')
    except KeyboardInterrupt:
        pass
    sys.exit(p.returncode)

from macimu import IMU
from motion_live import VibrationDetector

# ─── ANSI helpers ───
RST  = "\033[0m"
BOLD = "\033[1m"
DIM  = "\033[2m"
RED  = "\033[31m"
GRN  = "\033[32m"
YEL  = "\033[33m"
CYN  = "\033[36m"
BRED = "\033[91m"
BGRN = "\033[92m"
BYEL = "\033[93m"
BCYN = "\033[96m"
BWHT = "\033[97m"

BLOCKS = ' ▁▂▃▄▅▆▇█'


def live_bar(value, max_val, width=40):
    """Render a horizontal progress bar."""
    ratio = min(1.0, max(0.0, value / max(max_val, 1e-10)))
    filled = int(ratio * width)
    bar = '█' * filled + '░' * (width - filled)
    return bar


def phase1_baseline(imu, duration=5.0, fs=100):
    """Phase 1: Measure baseline noise while MacBook is still."""
    print(f"\n{BWHT}{'─' * 60}{RST}")
    print(f"  {BCYN}{BOLD}PHASE 1: BASELINE MEASUREMENT{RST}")
    print(f"  {DIM}Keep your MacBook completely still on a flat surface.{RST}")
    print(f"  {DIM}Measuring for {duration:.0f} seconds...{RST}")
    print(f"{BWHT}{'─' * 60}{RST}\n")

    # Countdown instead of input() — stdin is consumed by sudo pipe
    for i in range(3, 0, -1):
        sys.stdout.write(f"\r  {YEL}Starting in {i}...{RST}  ")
        sys.stdout.flush()
        time.sleep(1)
    print()

    detector = VibrationDetector(fs=fs)
    mags = []
    raw_samples = []
    start = time.time()

    while time.time() - start < duration:
        samples = imu.read_accel_timed()
        if not samples:
            time.sleep(0.01)
            continue

        for s in samples:
            detector.process(s.x, s.y, s.z, s.t)
            mag = math.sqrt(s.x**2 + s.y**2 + s.z**2)
            mags.append(mag)
            raw_samples.append((s.x, s.y, s.z))

        # Live progress
        elapsed = time.time() - start
        pct = min(1.0, elapsed / duration)
        bar = live_bar(pct, 1.0, 40)
        dyn = detector.latest_mag
        sys.stdout.write(f"\r  {GRN}{bar}{RST} {pct*100:5.1f}%  mag:{dyn:.6f}g  ")
        sys.stdout.flush()

    print(f"\n\n  {BGRN}✓ Baseline captured: {len(mags)} samples{RST}")

    # Compute statistics
    if not mags:
        print(f"  {RED}No samples collected! Check accelerometer access.{RST}")
        return None

    mean_mag = sum(mags) / len(mags)
    variance = sum((m - mean_mag) ** 2 for m in mags) / len(mags)
    std_mag = math.sqrt(variance)

    # Per-axis stats
    xs = [r[0] for r in raw_samples]
    ys = [r[1] for r in raw_samples]
    zs = [r[2] for r in raw_samples]

    baseline = {
        'mean_magnitude': mean_mag,
        'std_magnitude': std_mag,
        'noise_floor': std_mag * 2,  # 2-sigma noise floor
        'mean_x': sum(xs) / len(xs),
        'mean_y': sum(ys) / len(ys),
        'mean_z': sum(zs) / len(zs),
        'std_x': math.sqrt(sum((x - sum(xs)/len(xs))**2 for x in xs) / len(xs)),
        'std_y': math.sqrt(sum((y - sum(ys)/len(ys))**2 for y in ys) / len(ys)),
        'std_z': math.sqrt(sum((z - sum(zs)/len(zs))**2 for z in zs) / len(zs)),
        'sample_count': len(mags),
        'duration_s': duration,
    }

    print(f"  {DIM}Mean |g|     : {mean_mag:.6f}g{RST}")
    print(f"  {DIM}Std  |g|     : {std_mag:.6f}g{RST}")
    print(f"  {DIM}Noise floor  : {baseline['noise_floor']:.6f}g (2σ){RST}")
    print(f"  {DIM}Gravity axes : X={baseline['mean_x']:+.4f} Y={baseline['mean_y']:+.4f} Z={baseline['mean_z']:+.4f}{RST}")

    return baseline


def phase2_vibration(imu, baseline, duration=10.0, fs=100):
    """Phase 2: Measure vibration levels while user taps/vibrates."""
    print(f"\n{BWHT}{'─' * 60}{RST}")
    print(f"  {BCYN}{BOLD}PHASE 2: VIBRATION MEASUREMENT{RST}")
    print(f"  {DIM}Tap or vibrate your MacBook like you normally would.{RST}")
    print(f"  {DIM}Try different intensities: light taps, knocks, table bumps.{RST}")
    print(f"  {DIM}Measuring for {duration:.0f} seconds...{RST}")
    print(f"{BWHT}{'─' * 60}{RST}\n")

    # Countdown instead of input() — stdin is consumed by sudo pipe
    for i in range(3, 0, -1):
        sys.stdout.write(f"\r  {YEL}Start tapping in {i}...{RST}  ")
        sys.stdout.flush()
        time.sleep(1)
    print(f"\r  {BGRN}GO! Tap/vibrate now!{RST}              ")

    detector = VibrationDetector(fs=fs)
    dynamic_mags = []
    peak_mags = []
    event_amps = []
    start = time.time()
    last_evt_count = 0

    while time.time() - start < duration:
        samples = imu.read_accel_timed()
        if not samples:
            time.sleep(0.01)
            continue

        for s in samples:
            dyn = detector.process(s.x, s.y, s.z, s.t)
            if dyn is not None:
                dynamic_mags.append(dyn)

        # Track events
        current_count = len(detector.events)
        if current_count > last_evt_count:
            new_evts = list(detector.events)[-(current_count - last_evt_count):]
            last_evt_count = current_count
            for ev in new_evts:
                event_amps.append(ev['amp'])

        # Live display
        elapsed = time.time() - start
        pct = min(1.0, elapsed / duration)
        bar = live_bar(pct, 1.0, 30)
        cur_mag = detector.latest_mag
        evts = len(detector.events)

        # Mini live meter
        noise = baseline['noise_floor']
        ratio = min(1.0, cur_mag / max(noise * 20, 0.001))
        meter_w = 15
        meter_filled = int(ratio * meter_w)
        if ratio > 0.7:
            meter_color = BRED
        elif ratio > 0.3:
            meter_color = YEL
        else:
            meter_color = GRN
        meter = f"{meter_color}{'█' * meter_filled}{'░' * (meter_w - meter_filled)}{RST}"

        sys.stdout.write(f"\r  {GRN}{bar}{RST} {pct*100:5.1f}%  {meter}  evts:{evts}  ")
        sys.stdout.flush()

    print(f"\n\n  {BGRN}✓ Vibration data captured: {len(dynamic_mags)} samples, {len(event_amps)} events{RST}")

    if not dynamic_mags:
        print(f"  {RED}No vibration data collected!{RST}")
        return None

    # Filter out baseline noise from dynamic magnitudes
    noise_floor = baseline['noise_floor']
    vib_mags = [m for m in dynamic_mags if m > noise_floor]

    vibration = {
        'total_dynamic_samples': len(dynamic_mags),
        'above_noise_samples': len(vib_mags),
        'event_count': len(event_amps),
        'duration_s': duration,
    }

    if vib_mags:
        sorted_mags = sorted(vib_mags)
        vibration['min_vib'] = sorted_mags[0]
        vibration['max_vib'] = sorted_mags[-1]
        vibration['mean_vib'] = sum(vib_mags) / len(vib_mags)
        vibration['median_vib'] = sorted_mags[len(sorted_mags) // 2]
        vibration['p25_vib'] = sorted_mags[int(len(sorted_mags) * 0.25)]
        vibration['p75_vib'] = sorted_mags[int(len(sorted_mags) * 0.75)]
        vibration['p90_vib'] = sorted_mags[int(len(sorted_mags) * 0.90)]
        vibration['p95_vib'] = sorted_mags[int(len(sorted_mags) * 0.95)]

        print(f"  {DIM}Vibrations above noise : {len(vib_mags)} samples{RST}")
        print(f"  {DIM}Mean vibration         : {vibration['mean_vib']:.6f}g{RST}")
        print(f"  {DIM}Median vibration       : {vibration['median_vib']:.6f}g{RST}")
        print(f"  {DIM}Max vibration          : {vibration['max_vib']:.6f}g{RST}")
        print(f"  {DIM}90th percentile        : {vibration['p90_vib']:.6f}g{RST}")
    else:
        print(f"  {YEL}⚠ No vibrations above noise floor detected.{RST}")
        print(f"  {YEL}  Try tapping harder or bumping the table.{RST}")

    if event_amps:
        sorted_amps = sorted(event_amps)
        vibration['min_event_amp'] = sorted_amps[0]
        vibration['max_event_amp'] = sorted_amps[-1]
        vibration['mean_event_amp'] = sum(event_amps) / len(event_amps)
        vibration['median_event_amp'] = sorted_amps[len(sorted_amps) // 2]
        print(f"  {DIM}Event amplitudes       : {vibration['min_event_amp']:.6f}g - {vibration['max_event_amp']:.6f}g{RST}")

    return vibration


def compute_profile(baseline, vibration, sensitivity='medium'):
    """Compute calibration profile from measurements."""
    noise = baseline['noise_floor']

    # Sensitivity multipliers
    sens_map = {
        'low':    {'thresh_mult': 6.0, 'timeframe': 3.0, 'tolerance': 0.8, 'cooldown': 3.0},
        'medium': {'thresh_mult': 4.0, 'timeframe': 2.0, 'tolerance': 0.5, 'cooldown': 2.0},
        'high':   {'thresh_mult': 2.5, 'timeframe': 1.0, 'tolerance': 0.3, 'cooldown': 1.5},
    }
    s = sens_map.get(sensitivity, sens_map['medium'])

    # Calculate thresholds based on measured data
    trigger_threshold = noise * s['thresh_mult']

    # If we have vibration data, refine thresholds
    if vibration and vibration.get('mean_vib'):
        # Set trigger at a level that catches real vibrations but ignores noise
        # Use the 25th percentile of detected vibrations as the minimum detectable
        vib_low = vibration.get('p25_vib', vibration['mean_vib'] * 0.5)
        trigger_threshold = max(trigger_threshold, vib_low * 0.8)

    # Event severity filter - which event types to trigger on
    sev_filters = {
        'low':    ['CHOC_MAJEUR', 'CHOC_MOYEN'],
        'medium': ['CHOC_MAJEUR', 'CHOC_MOYEN', 'MICRO_CHOC', 'VIBRATION'],
        'high':   ['CHOC_MAJEUR', 'CHOC_MOYEN', 'MICRO_CHOC', 'VIBRATION', 'VIB_LEGERE'],
    }

    profile = {
        'version': 2,
        'created': datetime.datetime.now().isoformat(),
        'sensitivity': sensitivity,
        'baseline': {
            'mean_magnitude': baseline['mean_magnitude'],
            'std_magnitude': baseline['std_magnitude'],
            'noise_floor': noise,
            'gravity': {
                'x': baseline['mean_x'],
                'y': baseline['mean_y'],
                'z': baseline['mean_z'],
            },
        },
        'thresholds': {
            'trigger_magnitude': trigger_threshold,
            'timeframe': s['timeframe'],
            'tolerance': s['tolerance'],
            'cooldown': s['cooldown'],
            'severity_filter': sev_filters.get(sensitivity, sev_filters['medium']),
        },
        'sta_lta': {
            'thresh_on':  [3.0, 2.5, 2.0],
            'thresh_off': [1.5, 1.3, 1.2],
        },
    }

    # Add vibration stats if available
    if vibration:
        profile['vibration_stats'] = {
            'event_count': vibration.get('event_count', 0),
            'mean_vib': vibration.get('mean_vib'),
            'max_vib': vibration.get('max_vib'),
            'p90_vib': vibration.get('p90_vib'),
        }

    return profile


def print_profile_summary(profile):
    """Print a summary of the calibration profile."""
    print(f"\n{BWHT}{'─' * 60}{RST}")
    print(f"  {BCYN}{BOLD}CALIBRATION PROFILE SUMMARY{RST}")
    print(f"{BWHT}{'─' * 60}{RST}\n")

    sens = profile['sensitivity']
    sens_color = {'low': GRN, 'medium': YEL, 'high': BRED}.get(sens, DIM)

    print(f"  {BWHT}Sensitivity      :{RST} {sens_color}{BOLD}{sens.upper()}{RST}")
    print(f"  {BWHT}Noise floor      :{RST} {profile['baseline']['noise_floor']:.6f}g")
    print(f"  {BWHT}Trigger threshold:{RST} {profile['thresholds']['trigger_magnitude']:.6f}g")
    print(f"  {BWHT}Timeframe        :{RST} {profile['thresholds']['timeframe']:.1f}s")
    print(f"  {BWHT}Tolerance        :{RST} {profile['thresholds']['tolerance']:.1f}s")
    print(f"  {BWHT}Cooldown         :{RST} {profile['thresholds']['cooldown']:.1f}s")
    print(f"  {BWHT}Severity filter  :{RST} {', '.join(profile['thresholds']['severity_filter'])}")

    if 'vibration_stats' in profile and profile['vibration_stats'].get('mean_vib'):
        stats = profile['vibration_stats']
        print(f"\n  {DIM}─ Measured vibrations ─{RST}")
        print(f"  {DIM}Events detected  : {stats['event_count']}{RST}")
        print(f"  {DIM}Mean amplitude   : {stats['mean_vib']:.6f}g{RST}")
        print(f"  {DIM}Max amplitude    : {stats['max_vib']:.6f}g{RST}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate vibrate_player.py for your MacBook's accelerometer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sudo python3 calibrate.py                    # Interactive calibration
  sudo python3 calibrate.py --sensitivity high  # High sensitivity preset
  sudo python3 calibrate.py --quick             # Quick calibration (shorter measurements)
  sudo python3 calibrate.py --output my_cal.json # Custom output file
        """
    )
    parser.add_argument("--output", "-o", default="calibration.json",
                        help="Output calibration file (default: calibration.json)")
    parser.add_argument("--sensitivity", "-s", choices=['low', 'medium', 'high'],
                        default=None,
                        help="Sensitivity preset (default: ask interactively)")
    parser.add_argument("--quick", "-q", action="store_true",
                        help="Quick mode: shorter measurement durations")
    parser.add_argument("--baseline-duration", type=float, default=None,
                        help="Baseline measurement duration in seconds")
    parser.add_argument("--vibration-duration", type=float, default=None,
                        help="Vibration measurement duration in seconds")

    args = parser.parse_args()

    # Durations
    if args.quick:
        base_dur = args.baseline_duration or 3.0
        vib_dur = args.vibration_duration or 5.0
    else:
        base_dur = args.baseline_duration or 5.0
        vib_dur = args.vibration_duration or 10.0

    # Banner
    print(f"\n{BWHT}{'═' * 60}{RST}")
    print(f"  {BCYN}{BOLD}╔═══════════════════════════════════════╗{RST}")
    print(f"  {BCYN}{BOLD}║   VIBRATE PLAYER CALIBRATION TOOL     ║{RST}")
    print(f"  {BCYN}{BOLD}╚═══════════════════════════════════════╝{RST}")
    print(f"{BWHT}{'═' * 60}{RST}")
    print(f"  {DIM}This tool measures your MacBook's accelerometer and{RST}")
    print(f"  {DIM}creates a calibration profile for vibrate_player.py{RST}")
    print()

    # Phase 1: Baseline
    with IMU(sample_rate=100) as imu:
        baseline = phase1_baseline(imu, duration=base_dur, fs=100)
        if baseline is None:
            print(f"\n  {RED}Calibration failed at baseline phase.{RST}")
            sys.exit(1)

        # Phase 2: Vibration
        vibration = phase2_vibration(imu, baseline, duration=vib_dur, fs=100)

    # Choose sensitivity (interactive input doesn't work under sudo pipe)
    sensitivity = args.sensitivity
    if sensitivity is None:
        sensitivity = 'medium'
        print(f"\n  {DIM}No --sensitivity specified, using: {YEL}MEDIUM{RST}")

    # Compute profile
    profile = compute_profile(baseline, vibration, sensitivity)

    # Show summary
    print_profile_summary(profile)

    # Save
    output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        args.output
    )
    with open(output_path, 'w') as f:
        json.dump(profile, f, indent=2)

    print(f"  {BGRN}{BOLD}✓ Calibration saved to: {output_path}{RST}")
    print()
    print(f"  {BWHT}Usage:{RST}")
    print(f"  {DIM}  python3 vibrate_player.py --calibration {args.output}{RST}")
    print(f"  {DIM}  python3 vibrate_player.py --calibration {args.output} k.mp3{RST}")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {YEL}Calibration cancelled.{RST}")
        sys.exit(0)
    except Exception as e:
        print(f"\n  {RED}Error: {e}{RST}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
