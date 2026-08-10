import { Component, Input, Output, EventEmitter } from '@angular/core';

@Component({
  selector: 'app-llm-intervention',
  templateUrl: './intervention.html',
  styleUrls: ['./intervention.css']
})
export class LlmInterventionComponent {
  private _intervention: any = null;
  // Counts suggestions rather than tracking one. The panel stays mounted across
  // them, so the timer bar only replays its animation if its element is rebuilt
  // -- which the *ngFor over [tick] does, since the item it tracks changed.
  tick = 0;

  @Input() set intervention(value: any) {
    this._intervention = value;
    this.tick++;
  }
  get intervention(): any {
    return this._intervention;
  }

  // Off in the tutorial, where the panel is meant to stay put: a bar draining to
  // nothing beside a panel that never leaves reads as a broken promise.
  @Input() showTimer = false;

  @Output() explore = new EventEmitter<any>();
  @Output() closed = new EventEmitter<void>();

  close() {
    this.closed.emit();
  }
}
