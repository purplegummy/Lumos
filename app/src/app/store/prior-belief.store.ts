import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { map } from 'rxjs/operators';
import { PriorBelief, PriorBeliefKey } from '../models/prior-belief';

const STORAGE_KEY = 'lumos:priors';

@Injectable({ providedIn: 'root' })
export class PriorBeliefStore {

  private priors$ = new BehaviorSubject<Record<PriorBeliefKey, PriorBelief>>(this.loadFromStorage());

  all$ = this.priors$.asObservable();

  // to know when to show the modal
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
    this.saveToStorage(next);
  }

  removeBelief(datasetId: string, attribute: string): void {
    const next = { ...this.priors$.value };
    delete next[`${datasetId}::${attribute}`];
    this.priors$.next(next);
    this.saveToStorage(next);
  }

  private loadFromStorage(): Record<PriorBeliefKey, PriorBelief> {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  private saveToStorage(priors: Record<PriorBeliefKey, PriorBelief>): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(priors));
  }
}
