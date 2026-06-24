import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { map } from 'rxjs/operators';
import { PriorBelief } from '../models/prior-belief';

@Injectable({ providedIn: 'root' })
export class PriorBeliefStore {

  private priors$ = new BehaviorSubject<Record<string, PriorBelief>>({});

  all$ = this.priors$.asObservable();

  hasPriorsFor(datasetId: string): boolean {
    return Object.keys(this.priors$.value).some(k => k.startsWith(`${datasetId}::`));
  }

  hasPriorsFor$(datasetId: string) {
    return this.priors$.pipe(
      map(priors => Object.keys(priors).some(k => k.startsWith(`${datasetId}::`)))
    );
  }

  getBelief(datasetId: string, attribute: string): PriorBelief | undefined {
    return this.priors$.value[`${datasetId}::${attribute}`];
  }

  setBelief(belief: PriorBelief): void {
    const next = { ...this.priors$.value, [`${belief.datasetId}::${belief.attribute}`]: belief };
    this.priors$.next(next);
  }

  removeBelief(datasetId: string, attribute: string): void {
    const next = { ...this.priors$.value };
    delete next[`${datasetId}::${attribute}`];
    this.priors$.next(next);
  }
}
