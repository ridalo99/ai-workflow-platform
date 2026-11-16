package studio.workflow.approval;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class ApprovalService {
    private final long thresholdCents;

    public ApprovalService(@Value("${approval.threshold-cents}") long thresholdCents) {
        if (thresholdCents < 0) {
            throw new IllegalArgumentException("approval.threshold-cents must be non-negative");
        }
        this.thresholdCents = thresholdCents;
    }

    public ApprovalDecision evaluate(long amountCents) {
        var status = amountCents <= thresholdCents
                ? ApprovalDecision.Status.AUTO_APPROVED
                : ApprovalDecision.Status.MANUAL_REVIEW;
        return new ApprovalDecision(status, thresholdCents);
    }
}
