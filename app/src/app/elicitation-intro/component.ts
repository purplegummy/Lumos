import { Component } from "@angular/core";
import { ActivatedRoute, Router } from "@angular/router";

@Component({
  selector: "app-elicitation-intro",
  templateUrl: "./component.html",
  styleUrls: ["./component.scss"],
})
export class ElicitationIntroComponent {
  constructor(private router: Router, private route: ActivatedRoute) {}

  startElicitation(): void {
    const dest = this.route.snapshot.data["dest"] || "/elicitation";
    this.router.navigate([dest], { queryParamsHandling: "preserve" });
  }
}
