package studio.workflow.approval;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = "approval.threshold-cents=10000")
@AutoConfigureMockMvc
class ApprovalControllerTest {
    @Autowired
    MockMvc mvc;

    @ParameterizedTest
    @CsvSource({"0,AUTO_APPROVED", "9999,AUTO_APPROVED", "10000,AUTO_APPROVED", "10001,MANUAL_REVIEW"})
    void evaluatesAmountsAgainstConfiguredThreshold(long amountCents, String decision) throws Exception {
        mvc.perform(post("/api/approvals/evaluate")
                        .contentType("application/json")
                        .content("{\"amountCents\":" + amountCents + "}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value(decision))
                .andExpect(jsonPath("$.thresholdCents").value(10000));
    }

    @ParameterizedTest
    @ValueSource(strings = {"{}", "{\"amountCents\":null}", "{\"amountCents\":-1}",
            "{\"amountCents\":1.5}", "{\"amountCents\":\"123\"}", "{\"amountCents\":9223372036854775808}"})
    void rejectsInvalidAmounts(String body) throws Exception {
        mvc.perform(post("/api/approvals/evaluate").contentType("application/json").content(body))
                .andExpect(status().isBadRequest());
    }
}
