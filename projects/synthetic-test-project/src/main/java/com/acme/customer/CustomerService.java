package com.acme.customer;

import java.sql.Connection;
import java.util.List;

/**
 * Purpose:        Business entry point for customer processing.
 * Responsibility: Orchestrate the end-to-end flow that the build plan's §83
 *                 acceptance test traces:
 *                   entry point -> metadata lookups -> dynamic SQL -> DB read.
 * Notes:          This is the class an architecture question should identify as
 *                 "where the process starts". The method chain is:
 *                   processCustomer -> loadEligibleCustomers
 *                     -> MetadataService.getPhysicalTable / getColumnList
 *                     -> DynamicQueryBuilder.buildQuery
 *                     -> CustomerRepository.runResolvedQuery
 */
public class CustomerService {

    private static final String CUSTOMER_LOGICAL_NAME = "CUSTOMER_ELIGIBILITY";

    private final CustomerRepository repository;
    private final MetadataService metadataService;
    private final DynamicQueryBuilder queryBuilder;

    public CustomerService(Connection connection) {
        this.repository = new CustomerRepository(connection);
        this.metadataService = new MetadataService(connection);
        this.queryBuilder = new DynamicQueryBuilder();
    }

    /** Top-level use case invoked by the batch job / controller. */
    public int processCustomer(long custId) throws Exception {
        List<Object[]> customer = repository.findCustomer(custId);
        if (customer.isEmpty()) {
            return 0;
        }
        List<Object[]> eligible = loadEligibleCustomers("ACTIVE");
        return eligible.size();
    }

    /**
     * Determines customer eligibility. The physical table and the selected
     * columns are resolved from metadata at runtime, then a dynamic query is
     * built and executed.
     */
    public List<Object[]> loadEligibleCustomers(String status) throws Exception {
        String table = metadataService.getPhysicalTable(CUSTOMER_LOGICAL_NAME);
        List<String> columns = metadataService.getColumnList(CUSTOMER_LOGICAL_NAME);

        String sql = queryBuilder.buildQuery(table, columns);
        return repository.runResolvedQuery(sql);
    }
}
