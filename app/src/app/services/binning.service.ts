
import { Injectable } from '@angular/core';
import { Bin } from '../models/prior-belief';

const BIN_COUNT = 10;
const BALL_COUNT = 100;

@Injectable({ providedIn: 'root' })
export class BinningService {

  readonly binCount = BIN_COUNT;
  readonly ballCount = BALL_COUNT;

  computeBins(values: number[]): Bin[] {
    const min = values.reduce((a, b) => a < b ? a : b);
    const max = values.reduce((a, b) => a > b ? a : b);
    const step = (max - min) / BIN_COUNT;

    return Array.from({ length: BIN_COUNT }, (_, i) => {
      const lo = min + (i * step);
      const hi = min + ((i + 1) * step);
      // for each bin return a range of lo and hi, and a label like "20–60"
      const isLast = i === BIN_COUNT - 1;
      return {
        lo,
        hi,
        label: step >= 1
          ? `${Math.round(lo)}–${isLast ? Math.round(hi) : Math.round(hi) - 1}`
          : `${this.fmt(lo, step)}–${this.fmt(hi, step)}`,
      };
    });
  }

  emptyBallCounts(): number[] {
    return new Array(BIN_COUNT).fill(0);
  }

  private fmt(n: number, step: number): string {
    if (step >= 1) return `${Math.round(n)}`;
    if (step >= 0.1) return n.toFixed(1);
    return n.toFixed(2);
  }
}
