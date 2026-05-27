import { Component, EventEmitter, Input, Output, HostListener, OnChanges } from '@angular/core';
import { Bin } from '../../../models/prior-belief';

@Component({
  selector: 'app-bin-column',
  templateUrl: './bincol.html',
  styleUrls: ['./bincol.css']
})
export class BinColumnComponent implements OnChanges {
  @Input() bin: Bin = { lo: 0, hi: 0, label: '' };
  @Input() count: number = 0;
  @Input() maxBalls: number = 30;
  @Input() remaining: number = 30;

  @Output() increment = new EventEmitter<void>();
  @Output() decrement = new EventEmitter<void>();
  @Output() setCount = new EventEmitter<number>();

  rows: number[] = [];
  dragging = false;

  ngOnChanges() {
    this.rows = Array.from({ length: this.maxBalls }, (_, i) => this.maxBalls - i);
  }

  isFilled(row: number): boolean {
    return row <= this.count;
  }

  isClickable(row: number): boolean {
    return row < this.count || this.remaining >= (row - this.count);
  }

  onMouseDown(row: number) {
    this.dragging = true;
    this.trySet(row);
  }

  onMouseEnter(row: number) {
    if (this.dragging) this.trySet(row);
  }

  @HostListener('document:mouseup')
  onMouseUp() {
    this.dragging = false;
  }

  private trySet(row: number) {
    if (!this.isClickable(row)) return;
    this.setCount.emit(row);
  }
}
