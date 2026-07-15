import { Component, EventEmitter, Input, Output, OnInit, OnChanges, SimpleChanges } from '@angular/core';
import { Bin } from '../../../models/prior-belief';
import { BinningService } from 'src/app/services/binning.service';
@Component({
  selector: 'app-balls-into-bins',
  templateUrl: './bib.html',
  styleUrls: ['./bib.css']
})
export class BallsIntoBinsComponent implements OnInit, OnChanges {
  @Input() values: number[] = [];
  @Input() initialCounts: number[] = [];
  @Input() fixedBins: Bin[] = [];   // when set, skips computation from values
  @Output() countsChange = new EventEmitter<number[]>();

  bins: Bin[] = [];
  counts: number[] = [];

  constructor(public binningService: BinningService) {}

  ngOnInit() {
    this.syncBinsAndCounts();
  }

  ngOnChanges(changes: SimpleChanges) {
    // Recompute on ANY bin-defining input change (values/fixedBins), not just
    // initialCounts -- so bins, and therefore the counts length, never go stale
    // relative to the current data. (Mechanism 2)
    const binInputChanged =
      (changes['values'] && !changes['values'].firstChange) ||
      (changes['fixedBins'] && !changes['fixedBins'].firstChange);
    const initialCountsChanged =
      changes['initialCounts'] && !changes['initialCounts'].firstChange;
    if (binInputChanged || initialCountsChanged) {
      this.syncBinsAndCounts();
    }
  }

  /**
   * Single source of truth for bins + counts. Bins come only from the current
   * inputs; counts are always reconciled to bins.length so the emitted array
   * length is deterministic and matches the bin definition.
   */
  private syncBinsAndCounts() {
    this.bins = this.computeBinsForInputs();
    const reconciled = this.reconcileCounts(this.initialCounts, this.bins.length);
    this.counts = reconciled;
    // If a stale/mismatched loaded array had to be resized to the current bins,
    // push the corrected array up so the parent's draft (and the belief it will
    // save) stays consistent with binEdges -- never emit an old-length array
    // against freshly computed bins. (Mechanism 3)
    if (this.bins.length > 0 && reconciled.length !== this.initialCounts.length) {
      Promise.resolve().then(() => this.countsChange.emit(this.counts.slice()));
    }
  }

  /**
   * Bins are defined ONLY by real inputs -- no `|| 10` magic fallback. If there
   * is no data yet, bins stay empty and nothing is emitted until they are ready,
   * surfacing "not ready" instead of papering over it with a wrong length.
   * (Mechanism 1)
   */
  private computeBinsForInputs(): Bin[] {
    if (this.fixedBins.length > 0) return this.fixedBins;
    if (this.values.length > 0) return this.binningService.computeBins(this.values);
    return [];
  }

  /**
   * Fit a counts array to exactly `n` bins: use it as-is when the length already
   * matches, otherwise prefer the current bin definition -- keep overlapping
   * bins and zero-fill the rest -- guaranteeing len(counts) === bins.length.
   */
  private reconcileCounts(source: number[], n: number): number[] {
    if (n === 0) return [];
    if (source.length === n) return source.slice();
    const fitted = this.binningService.emptyCountsFor(n);
    const overlap = Math.min(n, source.length);
    for (let i = 0; i < overlap; i++) fitted[i] = source[i];
    return fitted;
  }
  trackByBinIndex(index: number): number {
    return index;
  }

  getRemaining(): number {
    return this.binningService.ballCount - this.counts.reduce((a, b) => a + b, 0);
  }

 increment(index: number) {
    if (this.getRemaining() === 0) return;
    this.counts = this.counts.map((c, i) => i === index ? c + 1 : c);
    this.countsChange.emit(this.counts);
  }

  decrement(index: number) {
    if (this.counts[index] === 0) return;
    this.counts = this.counts.map((c, i) => i === index ? c - 1 : c);
    this.countsChange.emit(this.counts);
  }

  setCount(index: number, value: number) {
    const delta = value - this.counts[index];
    if (delta > 0 && delta > this.getRemaining()) return;
    this.counts = this.counts.map((c, i) => i === index ? value : c);
    this.countsChange.emit(this.counts);
  }

}
