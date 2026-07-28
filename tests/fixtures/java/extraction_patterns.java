// Committed regression corpus for Java extraction.
//
// Every construct here exists because a specific extraction path can get it
// wrong. Deleting a case deletes the guard, so add rather than replace.
package com.example.billing;

import java.util.List;
import java.util.ArrayList;
import static java.util.Collections.emptyList;
import java.util.function.*;

/** Fields: the name lives under a declarator, never as a direct child. */
public class Invoice implements Payable {
    // static final => CONSTANT, plain field => VARIABLE. Same node type.
    private static final int MAX_LINES = 100;
    public static final String CURRENCY = "USD";
    private String customer;
    private List<Line> lines;
    // One declaration, two declarators: the extractor indexes the first.
    private int subtotal, total;

    public Invoice(String customer) {
        this.customer = customer;
        this.lines = new ArrayList<>();
    }

    // Overload: same name, different arity. Both must survive.
    public Invoice() {
        this("anonymous");
    }

    // Return type is a type_identifier sitting BEFORE the name.
    public String getCustomer() {
        return customer;
    }

    // Generic method: type_parameters sit between modifiers and the type.
    public <T extends Comparable<T>> List<T> sorted(List<T> input) {
        return input;
    }

    // Receiver-then-name: a positional scan would call this a call to `lines`.
    public int lineCount() {
        return lines.size();
    }

    // Constructor call — the callee is Line's constructor, not a method.
    public void addLine(String label) {
        Line line = new Line(label);
        lines.add(line);
    }

    // No modifiers node at all: package-private, so _java_field_kind must
    // survive a declaration whose first child is the type.
    void reset() {
        emptyList();
    }
}

/** Interface methods have no body — signature must still come out clean. */
interface Payable {
    String getCustomer();

    default boolean isFree() {
        return false;
    }
}

/** Enum constants are named by a field, not by a nested enum_constant. */
enum Status {
    DRAFT,
    ISSUED,
    PAID
}

/** Java 14+ record — omitted entirely from the original feature request. */
record Line(String label, int cents) {
    static Line empty() {
        return new Line("", 0);
    }
}

/** Annotation type — also omitted from the original feature request. */
@interface Reviewed {
    String by();
}
