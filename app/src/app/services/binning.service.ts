
import { Injectable } from '@angular/core';
import { Bin } from '../models/prior-belief';

const BALL_COUNT = 30;

@Injectable({ providedIn: 'root' })
export class BinningService {

  readonly ballCount = BALL_COUNT;

  computeBins(values: number[], isCurrency: boolean = false): Bin[] {
    const nums = values.map(Number);
    const rawMin = nums.reduce((a, b) => a < b ? a : b);
    const rawMax = nums.reduce((a, b) => a > b ? a : b);

    // One bin per integer value when range is small and all values are integers,
    // EXCEPT the last two values, which merge into one closed range bin, e.g.
    // days_physical_activity_week ends in "[6,7]" rather than a lone "7" bin
    // whose natural half-open edge (8) was never actually observed. Both edges
    // of that merged bin are real values, so it can safely close on both ends;
    // every other bin stays the plain half-open "[v, v+1)" it always was.
    const allIntegers = nums.every(v => Number.isInteger(v));
    const range = rawMax - rawMin;
    if (allIntegers && range <= 10) {
      const length = range + 1;
      const singleCount = length >= 2 ? length - 2 : length;
      const bins: Bin[] = Array.from({ length: singleCount }, (_, i) => {
        const v = rawMin + i;
        return { lo: v, hi: v + 1, label: this.rangeLabel(v, v + 1, 1, isCurrency, false) };
      });
      if (length >= 2) {
        const v0 = rawMin + length - 2;
        const v1 = rawMin + length - 1;
        bins.push({ lo: v0, hi: v1 + 1, label: `[${this.fmt(v0, 1, isCurrency)}, ${this.fmt(v1, 1, isCurrency)}]` });
      }
      return bins;
    }

    const rawStep = range / 10;
    const step = this.niceStep(rawStep);
    const min = Math.floor(rawMin / step) * step;
    const max = Math.ceil(rawMax / step) * step;
    const count = Math.round((max - min) / step);

    return Array.from({ length: count }, (_, i) => {
      const lo = min + i * step;
      const hi = min + (i + 1) * step;
      return { lo, hi, label: this.rangeLabel(lo, hi, step, isCurrency, i === count - 1) };
    });
  }

  emptyBallCounts(n = 10): number[] {
    return new Array(n).fill(0);
  }

  emptyCountsFor(n: number): number[] {
    return new Array(n).fill(0);
  }

  categoricalBins(categories: string[]): Bin[] {
    return categories.map((cat, i) => ({ lo: i, hi: i + 1, label: cat }));
  }

  private niceStep(rawStep: number): number {
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalized = rawStep / magnitude;
    let nice: number;
    if (normalized <= 1)      nice = 1;
    else if (normalized <= 2) nice = 2;
    else if (normalized <= 5) nice = 5;
    else                      nice = 10;
    return Math.max(nice * magnitude, 1);
  }

  private fmt(n: number, step: number, isCurrency: boolean = false): string {
    const digits = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
    const numStr = isCurrency ? n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }) : n.toFixed(digits);
    return isCurrency ? `$${numStr}` : numStr;
  }

  // Half-open interval notation: every bin includes its left edge and excludes
  // its right edge ("[lo, hi)"), EXCEPT the very last bin in the set, which
  // also includes its right edge ("[lo, hi]") since that edge is the max of
  // the data -- otherwise the single largest value would fall outside every bin.
  private rangeLabel(lo: number, hi: number, step: number, isCurrency: boolean, isLast: boolean): string {
    const close = isLast ? ']' : ')';
    return `[${this.fmt(lo, step, isCurrency)}, ${this.fmt(hi, step, isCurrency)}${close}`;
  }
}
