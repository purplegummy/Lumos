import { NgModule } from "@angular/core";
import { Routes, RouterModule } from "@angular/router";
import { MainActivityComponent } from "./main-activity/component";
import { SubmissionCompleteComponent } from "./submission-complete/component";
import { TaskIntroComponent } from "./task-intro/component";
import { ElicitationIntroComponent } from "./elicitation-intro/component";

const routes: Routes = [
  // Legacy combined flow: elicitation followed immediately by the main task in one
  // visit, one verification code at the end. Left untouched as a fallback.
  { path: "", component: MainActivityComponent },
  { path: "submitted", component: SubmissionCompleteComponent },
  { path: "task-intro", component: TaskIntroComponent, data: { dest: "/" } },

  // Split flow: elicitation and the main task are two independently-visitable
  // sessions (same participantId/Firestore doc, two separate verification codes).
  { path: "elicitation", component: MainActivityComponent, data: { mode: "elicitation" } },
  { path: "elicitation/submitted", component: SubmissionCompleteComponent, data: { mode: "elicitation" } },
  { path: "elicitation/task-intro", component: ElicitationIntroComponent, data: { dest: "/elicitation" } },
  { path: "main", component: MainActivityComponent, data: { mode: "main" } },
  { path: "main/submitted", component: SubmissionCompleteComponent, data: { mode: "main" } },
  { path: "main/task-intro", component: TaskIntroComponent, data: { dest: "/main" } },
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule],
})
export class AppRoutingModule {}
