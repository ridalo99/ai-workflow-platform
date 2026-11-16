package studio.workflow.approval;

import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ApprovalController {
    private final ApprovalService service;

    public ApprovalController(ApprovalService service) {
        this.service = service;
    }

    @PostMapping("/api/approvals/evaluate")
    public ApprovalDecision evaluate(@Valid @RequestBody ApprovalRequest request) {
        return service.evaluate(request.amountCents());
    }
}
