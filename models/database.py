import sqlite3

from config import DATABASE_PATH


def get_connection():

    connection = sqlite3.connect(

        DATABASE_PATH

    )


    connection.row_factory = (

        sqlite3.Row

    )


    return connection


def initialize_database():

    connection = get_connection()

    cursor = connection.cursor()


    cursor.execute(

        """

        CREATE TABLE IF NOT EXISTS lessons (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            subject TEXT,

            grade TEXT,

            language TEXT,

            lesson_json TEXT,

            audio_filename TEXT,

            video_filename TEXT,

            created_at TIMESTAMP

                DEFAULT CURRENT_TIMESTAMP

        )

        """

    )


    connection.commit()

    connection.close()


def save_lesson(

    title,

    subject,

    grade,

    language,

    lesson_json,

    audio_filename="",

    video_filename=""

):

    connection = get_connection()

    cursor = connection.cursor()


    cursor.execute(

        """

        INSERT INTO lessons (

            title,

            subject,

            grade,

            language,

            lesson_json,

            audio_filename,

            video_filename

        )

        VALUES (?, ?, ?, ?, ?, ?, ?)

        """,

        (

            title,

            subject,

            grade,

            language,

            lesson_json,

            audio_filename,

            video_filename

        )

    )


    connection.commit()


    lesson_id = cursor.lastrowid


    connection.close()


    return lesson_id