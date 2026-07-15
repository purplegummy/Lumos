import { Component, OnInit } from "@angular/core";
import { Router } from "@angular/router";
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
    public global: SessionPage,
    private utilsService: UtilsService
  ) {}

  ngOnInit(): void {
    const key = `lumos_submitted_${this.global.participantId}`;
    if (!localStorage.getItem(key)) {
      this.router.navigate(["/"], { queryParamsHandling: "preserve" });
      return;
    }
    this.verificationCode = this.utilsService.generateVerificationCode(this.global.participantId);
  }
}
