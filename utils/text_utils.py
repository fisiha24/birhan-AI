def lesson_to_speech_text(

    lesson

):

    parts = []


    greeting = lesson.get(

        "greeting",

        ""

    )


    if greeting:

        parts.append(

            greeting

        )


    introduction = lesson.get(

        "introduction",

        ""

    )


    if introduction:

        parts.append(

            introduction

        )


    objectives = lesson.get(

        "objectives",

        []

    )


    if objectives:

        parts.append(

            "Our learning objectives are:"

        )


        for objective in objectives:

            parts.append(

                objective

            )


    for section in lesson.get(

        "sections",

        []

    ):

        parts.append(

            section.get(

                "explanation",

                ""

            )

        )


        example = section.get(

            "example",

            ""

        )


        if example:

            parts.append(

                "For example: "

                + example

            )


    summary = lesson.get(

        "summary",

        ""

    )


    if summary:

        parts.append(

            "Let us summarize what we have learned."

        )


        parts.append(

            summary

        )


    return "\n\n".join(

        part.strip()

        for part in parts

        if part and part.strip()

    )