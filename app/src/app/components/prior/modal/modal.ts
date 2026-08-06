import { Component, Input, Output, EventEmitter, OnInit } from '@angular/core';
import { Bin, PriorBelief, PriorCondition } from '../../../models/prior-belief';
import { PriorBeliefStore } from '../../../store/prior-belief.store';
import { BinningService } from '../../../services/binning.service';
import { ChatService } from '../../../services/socket.service';
import { SessionPage } from '../../../models/config';
import { cleanAttr } from '../../../models/attribute-labels';

const CONDITION_LABEL: Record<PriorCondition, string> = {
  diagnosed:     'teenagers who have been diagnosed with depression or anxiety',
  not_diagnosed: 'teenagers who have not been diagnosed with depression or anxiety',
};

interface AttributeCopy {
  // Singular/plural noun for what a single token bin represents, e.g. "age" /
  // "age categories" -- plugged into the instruction paragraph's "distribute
  // tokens across the ___" / "how common you think each ___ is" phrasing.
  rangeNoun: string;
  rangeNounPlural: string;
  // A concrete "for example, ..." sentence illustrating the mechanic for
  // this specific attribute (mirrors the participant-facing wording agreed
  // for the real dataset's attributes).
  example: string;
}

// Attribute copy for both the real dataset (mental_health_data.csv) and the
// tutorial's housing stand-in (tutorial_data.csv), keyed by the attribute
// names used in config.ts for each. Any attribute not listed here falls back
// to genericAttributeCopy() below rather than crashing or showing nothing --
// that fallback has no formatting logic though, so anything that should read
// as currency (Price) or a formatted number (Lot Area) needs its own entry.
const ATTRIBUTE_COPY: Record<string, AttributeCopy> = {
  Price: {
    rangeNoun: 'price range',
    rangeNounPlural: 'price ranges',
    example: 'if you believe homes priced around $200,000 are the most common, place more tokens on that range than the others',
  },
  'Lot Area': {
    rangeNoun: 'lot size range',
    rangeNounPlural: 'lot size ranges',
    example: 'if you believe lots around 8,000 sq ft are the most common, place more tokens on that range than the others',
  },
  child_age_years: {
    rangeNoun: 'age',
    rangeNounPlural: 'age categories',
    example: 'if you believe 16-year-olds are the most common, place more tokens on age 16 than on the other ages',
  },
  screen_time_weekday: {
    rangeNoun: 'screen time range',
    rangeNounPlural: 'screen time ranges',
    example: 'if you believe 4 hours of screen time a day is the most common, place more tokens on that range than the others',
  },
  hours_sleep_weeknight: {
    rangeNoun: 'sleep range',
    rangeNounPlural: 'sleep ranges',
    example: 'if you believe 8 hours of sleep is the most common, place more tokens on that range than the others',
  },
  days_physical_activity_week: {
    rangeNoun: 'activity range',
    rangeNounPlural: 'activity ranges',
    example: 'if you believe 3 days of physical activity a week is the most common, place more tokens on that range than the others',
  },
  child_sex: {
    rangeNoun: 'sex',
    rangeNounPlural: 'sexes',
    example: 'if you believe girls are more common in this group than boys, place more tokens on girls',
  },
  difficulty_making_friends: {
    rangeNoun: 'difficulty level',
    rangeNounPlural: 'difficulty levels',
    example: 'if you believe "a little difficulty" is the most common response, place more tokens there than on the other options',
  },
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

  @Output() closed = new EventEmitter<void>();

  // Fixed shape for both distributions -- was participant-toggleable, no longer is.
  tokenShape: 'square' | 'circle' = 'square';

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

  // Each attribute is 2 screens internally (combined distribution, then a
  // shared confidence rating), but participants shouldn't see that -- "Step
  // X of Y" should count attributes, not screens, so it reads e.g. "1 of 6"
  // instead of "1 of 12" for a 6-attribute dataset.
  get totalSteps(): number { return this.allAttributes.length; }
  get currentStep(): number { return this.currentAttrIndex + 1; }

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

  // Falls back to this for any attribute not in ATTRIBUTE_COPY (e.g. the
  // tutorial's Price/Lot Area/Home Type) so the instruction paragraph never
  // breaks -- just reads a bit more generic than the real dataset's
  // hand-written copy.
  private genericAttributeCopy(attr: string): AttributeCopy {
    const noun = cleanAttr(attr).toLowerCase();
    const kind = this.isCategorical(attr) ? 'category' : 'range';
    return {
      rangeNoun: `${noun} ${kind}`,
      rangeNounPlural: `${noun} ${kind}s`,
      example: `if you believe a particular ${noun} ${kind} is the most common, place more tokens there than on the others`,
    };
  }

  get attrCopy(): AttributeCopy {
    if (!this.selectedAttribute) return this.genericAttributeCopy('');
    return ATTRIBUTE_COPY[this.selectedAttribute] ?? this.genericAttributeCopy(this.selectedAttribute);
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

  onCountsAChange(counts: number[]) {
    this.draftCountsA = counts;
    this.counterClassA = this.counterClassA === 'pop-a' ? 'pop-b' : 'pop-a';
  }

  onCountsBChange(counts: number[]) {
    this.draftCountsB = counts;
    this.counterClassB = this.counterClassB === 'pop-a' ? 'pop-b' : 'pop-a';
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

  close() {
    this.closed.emit();
  }
}
