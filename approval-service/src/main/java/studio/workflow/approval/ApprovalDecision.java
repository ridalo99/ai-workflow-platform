package studio.workflow.approval;

public record ApprovalDecision(Status status, long thresholdCents) {
    public enum Status { AUTO_APPROVED, MANUAL_REVIEW }
}
