import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { map } from 'rxjs/operators';
import { PriorBelief } from '../models/prior-belief';

const STORAGE_KEY = 'lumos:priors';

@Injectable({ providedIn: 'root' })
export class PriorBeliefStore {

  private priors$ = new BehaviorSubject<Record<string, PriorBelief>>(this.loadFromStorage());

  all$ = this.priors$.asObservable();

  // Checks if there are any priors for a given dataset + attribute, used to show completed status in ui
  hasPriorsFor(datasetId: string): boolean {
    return Object.keys(this.priors$.value).some(k => k.startsWith(`${datasetId}::`));
  }

  hasPriorsFor$(datasetId: string) {
    return this.priors$.pipe(
      map(priors => Object.keys(priors).some(k => k.startsWith(`${datasetId}::`)))
    );
  }

  // used in ui to load existing priors into the elicitation modal when a user clicks on an attribute
  getBelief(datasetId: string, attribute: string): PriorBelief | undefined {
    return this.priors$.value[`${datasetId}::${attribute}`];
  }

  setBelief(belief: PriorBelief): void {
    const next = { ...this.priors$.value, [`${belief.datasetId}::${belief.attribute}`]: belief };
    this.priors$.next(next);
    this.saveToStorage(next);
  }

  // used to reset 
  removeBelief(datasetId: string, attribute: string): void {
    const next = { ...this.priors$.value };
    delete next[`${datasetId}::${attribute}`];
    this.priors$.next(next);
    this.saveToStorage(next);
  }

  private loadFromStorage(): Record<string, PriorBelief> {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  private saveToStorage(priors: Record<string, PriorBelief>): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(priors));
  }
}
