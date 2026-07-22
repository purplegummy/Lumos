// libraries
import { Injectable } from "@angular/core";
import { Socket } from "ngx-socket-io";
import { map } from "rxjs/operators";

@Injectable()
export class ChatService {
  // When true, this service is a no-op: the tutorial (dummy-data) walkthrough
  // must never open a socket or emit real telemetry/logs.
  private tutorialMode = false;

  constructor(private vizSocket: Socket) {}

  setTutorialMode(isTutorial: boolean) {
    this.tutorialMode = isTutorial;
  }

  connectToSocket() {
    if (this.tutorialMode) return;
    this.vizSocket.connect();
  }

  removeAllListenersAndDisconnectFromSocket() {
    if (this.tutorialMode) return;
    this.vizSocket.removeAllListeners();
    this.vizSocket.disconnect();
  }

  sendMessageToSaveSessionLogs(data, participantId) {
    if (this.tutorialMode) return;
    let payload = {
      data: data,
      participantId: participantId,
    };
    this.vizSocket.emit("on_session_end_page_level_logs", payload);
  }

  sendMessageToSaveLogs() {
    if (this.tutorialMode) return;
    this.vizSocket.emit("on_save_logs", null);
  }

  sendMessageToRestartBiasComputation() {
    if (this.tutorialMode) return;
    this.vizSocket.emit("on_reset_bias_computation", null);
  }

  sendInteractionResponse(payload) {
    if (this.tutorialMode) return;
    this.vizSocket.emit("on_interaction", payload);
  }

  sendPriors(payload) {
    if (this.tutorialMode) return;
    console.log('[socket] sendPriors emitting, connected=', (this.vizSocket as any).ioSocket?.connected, payload);
    this.vizSocket.emit("on_commit_priors", payload);
  }

  sendSelectedSubjects(payload) {
    if (this.tutorialMode) return;
    this.vizSocket.emit("on_selected_subjects", payload);
  }

  sendTaskSubmission(payload) {
    if (this.tutorialMode) return;
    this.vizSocket.emit("on_task_submitted", payload);
  }

  getDisconnectEventResponse() {
    return this.vizSocket.fromEvent("disconnect").pipe(map((obj) => obj));
  }

  getConnectEventResponse() {
    return this.vizSocket.fromEvent("connect").pipe(map((obj) => obj));
  }

  getInteractionResponse() {
    return this.vizSocket
      .fromEvent("interaction_response")
      .pipe(map((obj) => obj));
  }

  getAttributeDistribution() {
    return this.vizSocket
      .fromEvent("attribute_distribution")
      .pipe(map((obj) => obj));
  }
}
