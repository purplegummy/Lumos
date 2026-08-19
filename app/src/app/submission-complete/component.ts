import { Component, OnInit } from "@angular/core";
import { ActivatedRoute, Router } from "@angular/router";
import { SessionPage } from "../models/config";
import { UtilsService } from "../services/utils.service";

@Component({
  selector: "app-submission-complete",
  templateUrl: "./component.html",
  styleUrls: ["./component.scss"],
})
export class SubmissionCompleteComponent implements OnInit {
  verificationCode = "";

  constructor(
    private router: Router,
    private route: ActivatedRoute,
    public global: SessionPage,
    private utilsService: UtilsService
  ) {}

  ngOnInit(): void {
    // Same "mode" route data as MainActivityComponent (see app-routing.module.ts):
    // "elicitation" / "main" for the split flow, undefined -> combined/legacy "/".
    const mode = this.route.snapshot.data["mode"] || "combined";
    this.global.sessionMode = mode;
    const isElicitation = mode === "elicitation";
    const prefix =
      mode === "elicitation" ? "lumos_elicitation_submitted"
      : mode === "main" ? "lumos_main_submitted"
      : "lumos_submitted";
    const key = `${prefix}_${this.global.participantId}`;
    if (!localStorage.getItem(key)) {
      const fallback = mode === "elicitation" ? "/elicitation" : mode === "main" ? "/main" : "/";
      this.router.navigate([fallback], { queryParamsHandling: "preserve" });
      return;
    }
    const refreshed = !!localStorage.getItem(this.utilsService.getRefreshedStorageKey(this.global.participantId));
    this.verificationCode = this.utilsService.generateVerificationCode(this.global.participantId, refreshed, isElicitation);
  }
}
