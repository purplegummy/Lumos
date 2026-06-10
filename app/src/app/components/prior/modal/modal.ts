import { Component, Input, Output, EventEmitter } from '@angular/core';
import { PriorBelief } from '../../../models/prior-belief';
import { PriorBeliefStore } from '../../../store/prior-belief.store';
import { BinningService } from '../../../services/binning.service';

@Component({
  selector: 'app-elicitation-modal',
  templateUrl: './modal.html',
  styleUrls: ['./modal.css']
})
export class ElicitationModalComponent {
  @Input() datasetId: string = '';
  @Input() attributes: string[] = [];
  @Input() columnValues: Record<string, number[]> = {};

  @Output() closed = new EventEmitter<void>();

  selectedAttribute: string | null = null;
  draftCounts: number[] = [];

  constructor(
    private store: PriorBeliefStore,
    public binningService: BinningService
  ) {}

  get currentValues(): number[] {
    if (!this.selectedAttribute) return [];
    return this.columnValues[this.selectedAttribute] || [];
  }

  get remaining(): number {
    return this.binningService.ballCount - this.draftCounts.reduce((a, b) => a + b, 0);
  }

  selectAttribute(attr: string) {
    this.selectedAttribute = attr;
    const existing = this.store.getBelief(this.datasetId, attr);
    this.draftCounts = existing ? existing.counts.slice() : this.binningService.emptyBallCounts();
  }
  // a and b classes alternate to trigger CSS animations on count change
  counterClass: 'pop-a' | 'pop-b' = 'pop-a';

  onCountsChange(counts: number[]) {
    this.draftCounts = counts;
    this.counterClass = this.counterClass === 'pop-a' ? 'pop-b' : 'pop-a';
  }

  hasPrior(attr: string): boolean {
    return !!this.store.getBelief(this.datasetId, attr);
  }
  // resets the counts for the currently selected attribute to empty
  // does not remove from store
  reset() {
    this.draftCounts = this.binningService.emptyBallCounts();
  }

  save() {
    if (!this.selectedAttribute) return;
    const bins = this.binningService.computeBins(this.currentValues);
    const belief: PriorBelief = {
      datasetId: this.datasetId,
      attribute: this.selectedAttribute,
      binEdges: bins.map(b => b.lo).concat(bins[bins.length - 1].hi),
      counts: this.draftCounts,
      ballCount: this.binningService.ballCount,
      columnHash: '',
      createdAt: Date.now()
    };
    this.store.setBelief(belief);
    this.selectedAttribute = null;
    this.draftCounts = [];
  }

  close() {
    this.closed.emit();
  }
}
