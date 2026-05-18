
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
      return {
        lo,
        hi,
        label: `${this.fmt(lo)}–${this.fmt(hi)}`,
      };
    });
  }

  emptyBallCounts(): number[] {
    return new Array(BIN_COUNT).fill(0);
  }

  private fmt(n: number): string {
    return n % 1 === 0 ? `${n}` : n.toFixed(1);
  }
}
