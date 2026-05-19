import { Component, EventEmitter, Input, Output } from '@angular/core';
import { Bin } from '../../../models/prior-belief';
@Component({
  selector: 'app-bin-column',
  templateUrl: './bincol.html',
  styleUrls: ['./bincol.css']
})
export class BinColumnComponent {
  @Input() bin: Bin = { lo: 0, hi: 0, label: '' };
  @Input() count: number = 0;
  @Input() maxBalls: number = 100;
  @Input() remaining: number = 100;

  @Output() increment = new EventEmitter<void>();
  @Output() decrement = new EventEmitter<void>();

  ballArray(): number[] {
    return new Array(this.count).fill(0);
  }
}
