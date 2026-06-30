// Top-level build file. Plugin versions are declared here and applied in app/build.gradle.kts.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    // Kotlin 2.0 ships the Compose compiler as a Kotlin plugin; version tracks Kotlin's.
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
}
