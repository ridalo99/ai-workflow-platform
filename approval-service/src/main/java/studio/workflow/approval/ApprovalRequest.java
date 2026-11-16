package studio.workflow.approval;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.PositiveOrZero;

public record ApprovalRequest(@NotNull @PositiveOrZero Long amountCents) {}
