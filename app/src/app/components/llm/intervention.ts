import { Component, Input, Output, EventEmitter } from '@angular/core';

@Component({
  selector: 'app-llm-intervention',
  templateUrl: './intervention.html',
  styleUrls: ['./intervention.css']
})
export class LlmInterventionComponent {
  @Input() intervention: any = null;

  @Output() explore = new EventEmitter<any>();
  @Output() closed = new EventEmitter<void>();

  close() {
    this.closed.emit();
  }
}
