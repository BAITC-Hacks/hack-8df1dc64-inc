"""Audit the actual hourly training set and unchanged curve at low wind speeds."""
import argparse
import json
from collections import Counter
from itertools import chain
from math import floor
from pathlib import Path
from statistics import mean

from backend.model import HISTORY_END, build_curves, interpolate, utc_text
from data.history import RAW, TURBINES, hourly_history, iter_observations, verify_sources


def audit(output_dir, timestamp_role):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = verify_sources()
    observations = chain.from_iterable(iter_observations(RAW / entry['path'], entry['turbine_id'])
                                       for entry in manifest['files'])
    history = hourly_history(observations, HISTORY_END, timestamp_role)
    curves = build_curves(history['points'], HISTORY_END, TURBINES)
    report = {'cutoff': utc_text(HISTORY_END), 'timestamp_role': timestamp_role,
              'timestamp_role_confirmed': False,
              'source_hashes': {entry['turbine_id']: entry['sha256_uncompressed'] for entry in manifest['files']},
              'excluded_incomplete_hours': history['excluded_incomplete_hours'],
              'excluded_test_rows': history['excluded_test_rows'], 'turbines': []}
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for column, curve in enumerate(curves):
        turbine = curve['turbine_id']
        rows = [row for row in history['points'] if row['turbine_id'] == turbine]
        winds = [row['wind_speed_ms'] for row in rows]
        counts = Counter(floor(wind) for wind in winds)
        bins = [{'left_ms': left, 'right_ms': left + 1, 'hours': counts[left]}
                for left in range(max(counts) + 1)]
        low = [row for row in rows if row['wind_speed_ms'] <= 2]
        probes = [{'wind_speed_ms': speed, 'normalized_power': interpolate(curve['curve_knots'], speed)[0],
                   'extrapolated': interpolate(curve['curve_knots'], speed)[1]}
                  for speed in (0, 0.5, 1, 1.5, 2)]
        assert sum(bucket['hours'] for bucket in bins) == curve['hours'] == len(rows)
        assert all(0 <= probe['normalized_power'] <= 1 for probe in probes)
        item = {'turbine_id': turbine, 'hours': len(rows), 'min_wind_ms': min(winds),
                'max_wind_ms': max(winds), 'hours_le_2_ms': len(low),
                'share_le_2_ms_percent': 100 * len(low) / len(rows),
                'mean_observed_power_le_2_ms': mean(row['normalized_power'] for row in low) if low else None,
                'first_curve_knot': curve['curve_knots'][0], 'low_wind_predictions': probes,
                'histogram_1_ms': bins}
        report['turbines'].append(item)
        ax = axes[0, column]
        ax.bar([b['left_ms'] + 0.5 for b in bins], [b['hours'] for b in bins], width=0.9,
               color=['#a44929' if b['right_ms'] <= 2 else '#566d76' for b in bins])
        ax.set(title=f"{turbine}: {len(rows):,} training hours", xlabel='Hourly mean wind (m/s)', ylabel='Hours')
        ax.text(0.97, 0.95, f"Wind <= 2 m/s: {len(low):,} hours ({item['share_le_2_ms_percent']:.2f}%)",
                transform=ax.transAxes, ha='right', va='top', fontsize=9)
        ax = axes[1, column]
        grid = [i / 100 for i in range(401)]
        ax.plot(grid, [interpolate(curve['curve_knots'], wind)[0] for wind in grid], color='#a44929', label='Current curve')
        knots = [knot for knot in curve['curve_knots'] if knot['wind_speed_ms'] <= 4]
        ax.scatter([knot['wind_speed_ms'] for knot in knots], [knot['normalized_power'] for knot in knots],
                   color='#263d46', zorder=3, label='Training bin means')
        ax.axvline(curve['curve_knots'][0]['wind_speed_ms'], color='#566d76', linestyle='--', label='First knot')
        ax.set(xlim=(0, 4), ylim=(0, 0.15), xlabel='Wind (m/s)', ylabel='Normalized power', title='Low-wind response (unchanged model)')
        ax.legend(fontsize=8)
        ax.grid(axis='y', alpha=0.25)
    fig.suptitle(f'Historical training wind and model response | cutoff {utc_text(HISTORY_END)}\nTimestamp role: {timestamp_role} (assumption)', fontsize=12)
    fig.savefig(output_dir / 'distribution.png', dpi=150)
    plt.close(fig)
    (output_dir / 'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timestamp-role', choices=('start', 'end'), required=True)
    args = parser.parse_args()
    report = audit(args.output_dir, args.timestamp_role)
    for item in report['turbines']:
        print(json.dumps({key: value for key, value in item.items() if key != 'histogram_1_ms'}))


if __name__ == '__main__':
    main()
