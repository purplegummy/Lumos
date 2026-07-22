import { Component, Input, Output, EventEmitter, OnInit } from '@angular/core';
import { Bin, PriorBelief, PriorCondition } from '../../../models/prior-belief';
import { PriorBeliefStore } from '../../../store/prior-belief.store';
import { BinningService } from '../../../services/binning.service';
import { ChatService } from '../../../services/socket.service';
import { SessionPage } from '../../../models/config';
import { cleanAttr } from '../../../models/attribute-labels';

const CONDITION_LABEL: Record<PriorCondition, string> = {
  diagnosed:     'teens who have been diagnosed with depression or anxiety',
  not_diagnosed: 'teens who have not been diagnosed with depression or anxiety',
};

@Component({
  selector: 'app-elicitation-modal',
  templateUrl: './modal.html',
  styleUrls: ['./modal.css']
})
export class ElicitationModalComponent implements OnInit {
  @Input() datasetId: string = '';
  @Input() attributes: string[] = [];
  @Input() columnValues: Record<string, number[]> = {};
  @Input() categoricalValues: Record<string, string[]> = {};
  @Input() practiceMode: boolean = false;
  // Overrides CONDITION_LABEL below — used by the tutorial to swap in
  // domain-neutral wording for its unrelated dummy dataset, without touching
  // the real task's "diagnosed/not diagnosed" copy.
  @Input() conditionLabels: Partial<Record<PriorCondition, string>> = {};
  // Same idea as conditionLabels: the modal's instruction copy says "...among
  // these teens" by default (the real dataset). Tutorial overrides this to
  // match its own unrelated dummy dataset.
  @Input() subjectNounPlural: string = 'teens';

  @Output() closed = new EventEmitter<void>();

  currentAttrIndex = 0;
  done = false;
  // Both conditions' distributions are placed together on one screen now, so
  // there's only one flag needed per attribute: are we placing tokens, or
  // rating confidence in what was just placed.
  confidenceStep = false;
  draftCountsA: number[] = []; // 'diagnosed'
  draftCountsB: number[] = []; // 'not_diagnosed'
  confidenceRating: number = 50;
  private pendingBeliefA: PriorBelief | null = null;
  private pendingBeliefB: PriorBelief | null = null;

  get selectedAttribute(): string {
    return this.allAttributes[this.currentAttrIndex] ?? '';
  }

  // Each attribute is 2 screens now (combined distribution, then one shared
  // confidence rating), so the counter should reflect actual screens, not
  // just attributes -- otherwise "Step X of Y" shows the same number for
  // both screens of an attribute.
  get totalSteps(): number { return this.allAttributes.length * 2; }
  get currentStep(): number { return this.currentAttrIndex * 2 + (this.confidenceStep ? 2 : 1); }

  get isFirstStep(): boolean {
    return this.currentAttrIndex === 0 && !this.confidenceStep;
  }

  constructor(
    private store: PriorBeliefStore,
    public binningService: BinningService,
    private chatService: ChatService,
    private global: SessionPage
  ) {}

  ngOnInit() {
    this._loadDraft();
  }

  get allAttributes(): string[] {
    return this.attributes.concat(Object.keys(this.categoricalValues));
  }

  labelFor(condition: PriorCondition): string {
    return this.conditionLabels[condition] ?? CONDITION_LABEL[condition];
  }

  cleanAttr = cleanAttr;

  isCategorical(attr: string): boolean {
    return attr in this.categoricalValues;
  }

  get currentValues(): number[] {
    if (!this.selectedAttribute || this.isCategorical(this.selectedAttribute)) return [];
    return this.columnValues[this.selectedAttribute] || [];
  }

  get currentFixedBins(): Bin[] {
    if (!this.selectedAttribute || !this.isCategorical(this.selectedAttribute)) return [];
    return this.binningService.categoricalBins(this.categoricalValues[this.selectedAttribute]);
  }

  get remainingA(): number {
    return this.binningService.ballCount - this.draftCountsA.reduce((a, b) => a + b, 0);
  }
  get remainingB(): number {
    return this.binningService.ballCount - this.draftCountsB.reduce((a, b) => a + b, 0);
  }

  private _emptyCounts(): number[] {
    if (!this.selectedAttribute) return [];
    if (this.isCategorical(this.selectedAttribute)) {
      return this.binningService.emptyCountsFor(this.categoricalValues[this.selectedAttribute].length);
    }
    const bins = this.binningService.computeBins(this.currentValues);
    return this.binningService.emptyCountsFor(bins.length);
  }

  private _loadDraft() {
    if (!this.selectedAttribute) return;
    const existingA = this.store.getBelief(this.datasetId, this.selectedAttribute, 'diagnosed');
    const existingB = this.store.getBelief(this.datasetId, this.selectedAttribute, 'not_diagnosed');
    this.draftCountsA = existingA ? existingA.counts.slice() : this._emptyCounts();
    this.draftCountsB = existingB ? existingB.counts.slice() : this._emptyCounts();
  }

  counterClassA: 'pop-a' | 'pop-b' = 'pop-a';
  counterClassB: 'pop-a' | 'pop-b' = 'pop-a';

  // Lets participants collapse either distribution's grid (across all bins
  // at once) to cut down on vertical space, without losing track of counts.
  seriesACollapsed = false;
  seriesBCollapsed = false;
  toggleSeriesA() { this.seriesACollapsed = !this.seriesACollapsed; }
  toggleSeriesB() { this.seriesBCollapsed = !this.seriesBCollapsed; }

  onCountsAChange(counts: number[]) {
    this.draftCountsA = counts;
    this.counterClassA = this.counterClassA === 'pop-a' ? 'pop-b' : 'pop-a';
  }

  onCountsBChange(counts: number[]) {
    this.draftCountsB = counts;
    this.counterClassB = this.counterClassB === 'pop-a' ? 'pop-b' : 'pop-a';
  }

  hasPrior(attr: string): boolean {
    return this.store.hasBothBeliefs(this.datasetId, attr);
  }

  resetA() {
    this.draftCountsA = this._emptyCounts();
  }
  resetB() {
    this.draftCountsB = this._emptyCounts();
  }

  private _uniformFill(n: number): number[] {
    const base = Math.floor(this.binningService.ballCount / n);
    const remainder = this.binningService.ballCount % n;
    return Array.from({ length: n }, (_, i) => base + (i < remainder ? 1 : 0));
  }

  setUniformA() {
    if (this.draftCountsA.length === 0) return;
    this.draftCountsA = this._uniformFill(this.draftCountsA.length);
  }
  setUniformB() {
    if (this.draftCountsB.length === 0) return;
    this.draftCountsB = this._uniformFill(this.draftCountsB.length);
  }

  get canSave(): boolean {
    return this.remainingA === 0 && this.remainingB === 0;
  }

  save() {
    if (!this.selectedAttribute || !this.canSave) return;
    const buildBelief = (condition: PriorCondition, counts: number[]): PriorBelief => {
      if (this.isCategorical(this.selectedAttribute)) {
        const cats = this.categoricalValues[this.selectedAttribute];
        return {
          datasetId: this.datasetId,
          attribute: this.selectedAttribute,
          condition,
          binEdges: cats.map((_, i) => i).concat(cats.length),
          counts,
          ballCount: this.binningService.ballCount,
          columnHash: '',
          createdAt: Date.now(),
          categories: cats
        };
      }
      const bins = this.binningService.computeBins(this.currentValues);
      return {
        datasetId: this.datasetId,
        attribute: this.selectedAttribute,
        condition,
        binEdges: bins.map(b => b.lo).concat(bins[bins.length - 1].hi),
        counts,
        ballCount: this.binningService.ballCount,
        columnHash: '',
        createdAt: Date.now()
      };
    };
    this.pendingBeliefA = buildBelief('diagnosed', this.draftCountsA);
    this.pendingBeliefB = buildBelief('not_diagnosed', this.draftCountsB);
    this.confidenceStep = true;
    const savedA = this.store.getBelief(this.datasetId, this.selectedAttribute, 'diagnosed');
    const savedB = this.store.getBelief(this.datasetId, this.selectedAttribute, 'not_diagnosed');
    this.confidenceRating = savedA?.confidence ?? savedB?.confidence ?? 50;
  }

  submitConfidence() {
    if (!this.pendingBeliefA || !this.pendingBeliefB) return;
    const confidence = Number(this.confidenceRating);
    const beliefA = { ...this.pendingBeliefA, confidence };
    const beliefB = { ...this.pendingBeliefB, confidence };
    this.store.setBelief(beliefA);
    this.store.setBelief(beliefB);
    this.chatService.sendPriors({
      participantId: this.global.participantId,
      participantIdSource: this.global.participantIdSource,
      appMode: this.global.appMode,
      appType: this.global.appType,
      appLevel: this.global.appLevel,
      priors: beliefA,
    });
    this.chatService.sendPriors({
      participantId: this.global.participantId,
      participantIdSource: this.global.participantIdSource,
      appMode: this.global.appMode,
      appType: this.global.appType,
      appLevel: this.global.appLevel,
      priors: beliefB,
    });
    this.pendingBeliefA = null;
    this.pendingBeliefB = null;
    this.confidenceStep = false;
    this.confidenceRating = 50;
    this._nextAttribute();
  }

  private _nextAttribute() {
    if (this.currentAttrIndex < this.allAttributes.length - 1) {
      this.currentAttrIndex++;
      this.confidenceStep = false;
      this.confidenceRating = 50;
      this._loadDraft();
    } else {
      this.done = true;
    }
  }

  goBack() {
    if (this.confidenceStep) {
      // The in-progress token placement is still in draftCountsA/B — just
      // return to it, nothing to reload.
      this.confidenceStep = false;
      this.pendingBeliefA = null;
      this.pendingBeliefB = null;
    } else if (this.currentAttrIndex > 0) {
      this.currentAttrIndex--;
      this._loadDraft();
      this.confidenceStep = true;
      const existingA = this.store.getBelief(this.datasetId, this.selectedAttribute, 'diagnosed');
      const existingB = this.store.getBelief(this.datasetId, this.selectedAttribute, 'not_diagnosed');
      this.pendingBeliefA = existingA ? { ...existingA } : null;
      this.pendingBeliefB = existingB ? { ...existingB } : null;
      this.confidenceRating = existingA?.confidence ?? existingB?.confidence ?? 50;
    }
    // if isFirstStep, back button is hidden — nothing to do
  }

  get canClose(): boolean {
    return this.done || this.allAttributes.every(attr => this.hasPrior(attr));
  }

  close() {
    this.closed.emit();
  }
}
