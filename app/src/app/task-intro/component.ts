import { Component } from "@angular/core";
import { Router } from "@angular/router";

@Component({
  selector: "app-task-intro",
  templateUrl: "./component.html",
  styleUrls: ["./component.scss"],
})
export class TaskIntroComponent {
  constructor(private router: Router) {}

  startTask(): void {
    this.router.navigate(["/"], { queryParamsHandling: "preserve" });
  }
}
