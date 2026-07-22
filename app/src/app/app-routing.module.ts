import { NgModule } from "@angular/core";
import { Routes, RouterModule } from "@angular/router";
import { MainActivityComponent } from "./main-activity/component";
import { SubmissionCompleteComponent } from "./submission-complete/component";
import { TaskIntroComponent } from "./task-intro/component";

const routes: Routes = [
  { path: "", component: MainActivityComponent },
  { path: "submitted", component: SubmissionCompleteComponent },
  { path: "task-intro", component: TaskIntroComponent },
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule],
})
export class AppRoutingModule {}
