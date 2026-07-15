import { NgModule } from "@angular/core";
import { Routes, RouterModule } from "@angular/router";
import { MainActivityComponent } from "./main-activity/component";
import { SubmissionCompleteComponent } from "./submission-complete/component";

const routes: Routes = [
  { path: "", component: MainActivityComponent },
  { path: "submitted", component: SubmissionCompleteComponent },
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule],
})
export class AppRoutingModule {}
